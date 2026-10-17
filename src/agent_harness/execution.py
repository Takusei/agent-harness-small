"""Docker transport; no model-selected commands are ever executed on the host."""

import asyncio
import json
import os
import uuid
from pathlib import Path

from agent_harness import worker


class DockerExecutor:
    def __init__(
        self,
        workspace: Path,
        artifacts: Path,
        image: str,
        output_bytes: int = 16_000,
        command_seconds: float = 30,
    ):
        self.workspace = workspace.resolve(strict=True)
        if not self.workspace.is_dir() or "," in str(self.workspace):
            raise ValueError("Workspace must be a directory with no comma in its path")
        self.artifacts = artifacts
        artifacts.mkdir(parents=True, exist_ok=True)
        self.image = image
        self.output_bytes = output_bytes
        self.command_seconds = command_seconds
        self.reads: dict[str, str] = {}
        self.active: str | None = None
        self.cleanup_errors: list[str] = []

    def docker_args(self, name):
        uid = os.getuid() or 65532
        gid = os.getgid() if os.getuid() else 65532
        return [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--name",
            name,
            "-i",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=64",
            "--memory=256m",
            "--cpus=1",
            "--user",
            f"{uid}:{gid}",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=64m",
            "--workdir=/workspace",
            "--env=HOME=/tmp",
            "--env=PYTHONDONTWRITEBYTECODE=1",
            "--env=PYTHONUNBUFFERED=1",
            "--mount",
            f"type=bind,source={self.workspace},target=/workspace",
            "--entrypoint=python",
            self.image,
        ]

    async def _remove(self, name):
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                "rm",
                "-f",
                name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(process.communicate(), 10)
            if process.returncode and b"No such container" not in stderr:
                self.cleanup_errors.append(
                    f"Could not remove {name}: {stderr[:500].decode(errors='replace')}"
                )
        except (OSError, TimeoutError) as exc:
            self.cleanup_errors.append(f"Cleanup failed for {name}: {type(exc).__name__}")
        finally:
            if process and process.returncode is None:
                process.kill()
                await process.wait()

    async def run(self, args: list[str], stdin: bytes = b"", timeout: float | None = None):
        name = f"agent-harness-{uuid.uuid4().hex}"
        self.active = name
        process = None
        streams = {}
        reason = None
        total = 0
        tasks = []

        async def collect(kind, stream):
            nonlocal total
            path = self.artifacts / f"{name}.{kind}"
            preview = bytearray()
            size = 0
            streams[kind] = {"artifact": path.name}
            with path.open("wb") as handle:
                while chunk := await stream.read(8192):
                    # Hard disk cap across both streams, with bounded previews.
                    available = max(0, 1_000_000 - total)
                    handle.write(chunk[:available])
                    total += len(chunk)
                    size += len(chunk)
                    preview.extend(chunk[: max(0, self.output_bytes - len(preview))])
                    streams[kind].update(
                        text=preview.decode(errors="replace"), truncated=size > self.output_bytes
                    )
                    if total > 1_000_000:
                        raise OverflowError("Command output limit exceeded")
            streams[kind].setdefault("text", "")
            streams[kind].setdefault("truncated", False)

        async def communicate():
            process.stdin.write(stdin)
            await process.stdin.drain()
            process.stdin.close()
            await asyncio.gather(*tasks)
            await process.wait()

        try:
            process = await asyncio.create_subprocess_exec(
                *self.docker_args(name),
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            tasks = [
                asyncio.create_task(collect("stdout", process.stdout)),
                asyncio.create_task(collect("stderr", process.stderr)),
            ]
            try:
                await asyncio.wait_for(communicate(), timeout or self.command_seconds)
            except TimeoutError:
                reason = "timeout"
            except OverflowError:
                reason = "output_limit"
            except (BrokenPipeError, ConnectionResetError):
                await asyncio.gather(*tasks)
                await process.wait()
        finally:

            async def cleanup():
                # Terminating the docker CLI alone does not terminate its container.
                await self._remove(name)
                if process and process.returncode is None:
                    process.kill()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if process:
                    # A stopped reader may leave a full pipe. Drain after cancelling
                    # collectors, otherwise Process.wait can hang even after kill.
                    await asyncio.wait_for(process.communicate(), 5)
                self.active = None

            # A run deadline or signal during cleanup must not abandon removal.
            cleanup_task = asyncio.create_task(cleanup())
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                await cleanup_task
                raise
        return {
            "exit_code": process.returncode,
            "stop_reason": reason,
            "stdout": streams.get("stdout", {}),
            "stderr": streams.get("stderr", {}),
        }

    async def shell(self, command: str):
        self.reads.clear()
        # Python is the fixed entrypoint. It execs the shell only inside Docker.
        return await self.run(
            ["-I", "-c", "import os,sys; os.execv('/bin/sh', ['sh','-c',sys.argv[1]])", command]
        )

    async def file(self, name: str, arguments: dict):
        request = {"op": name, **arguments}
        if name == "edit_file":
            request["expected_hash"] = self.reads.get(arguments["path"])
            self.reads.clear()
        result = await self.run(
            ["-I", "-", json.dumps(request)], Path(worker.__file__).read_bytes()
        )
        if result["exit_code"] or result["stop_reason"]:
            return {"error": "Container file operation failed", "execution": result}
        path = self.artifacts / result["stdout"]["artifact"]
        try:
            payload = json.loads(path.read_text())
        except (ValueError, UnicodeError):
            return {"error": "Invalid file tool response", "execution": result}
        if name == "read_file" and "sha256" in payload:
            self.reads[arguments["path"]] = payload["sha256"]
        return payload
