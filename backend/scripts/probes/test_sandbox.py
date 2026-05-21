"""D2 smoke test — drive build_sandbox + exercise both backends end-to-end."""
from __future__ import annotations

import asyncio
import logging
import uuid

logging.basicConfig(level=logging.INFO)

from app.agents.harness.sandbox import build_sandbox


async def main() -> None:
    print("--- build_sandbox(local) ---")
    cap, backend = build_sandbox(policy={"sandbox": "local", "session_id": "test-local"})
    print(f"cap={type(cap).__name__ if cap else None}  backend={type(backend).__name__ if backend else None}")
    assert backend is not None

    print("\n--- LocalBackend round trip ---")
    w = backend.write("hello.txt", "hi from senharness\n")
    print(f"  write: {w!r}")
    r = backend.read("hello.txt")
    print(f"  read:  {r!r}")
    ls = backend.ls_info(".")
    print(f"  ls:    {ls!r}")
    ex = backend.execute("python -c 'print(2+40)'")
    print(f"  execute: rc={ex.exit_code} output={ex.output!r}")

    print("\n--- build_sandbox(docker) ---")
    cap_d, backend_d = build_sandbox(
        policy={
            "sandbox": {"kind": "docker", "image": "python:3.12-slim"},
            "session_id": f"test-docker-{uuid.uuid4().hex[:8]}",
        }
    )
    print(f"  cap={type(cap_d).__name__ if cap_d else None}  backend={type(backend_d).__name__ if backend_d else None}")
    if backend_d is None:
        print("  DockerSandbox not instantiable — skipping")
        return

    print("\n--- DockerSandbox round trip (pulls python:3.12-slim if needed) ---")
    try:
        backend_d.write("solve.py", "print(sum(i*i for i in range(1,11)))\n")
        out = backend_d.execute("python solve.py")
        print(f"  run solve.py: rc={out.exit_code} output={out.output!r}")
        out2 = backend_d.execute("python -c 'import sys; print(sys.version)'")
        print(f"  python version: rc={out2.exit_code} output={out2.output!r}")
    except Exception as exc:
        import traceback

        traceback.print_exc()
        print(f"  docker error: {type(exc).__name__}: {exc}")
    finally:
        for fn in ("stop", "close", "cleanup", "__exit__"):
            m = getattr(backend_d, fn, None)
            if callable(m):
                try:
                    if fn == "__exit__":
                        m(None, None, None)
                    else:
                        m()
                    print(f"  cleanup via {fn}()")
                    break
                except Exception:
                    continue


if __name__ == "__main__":
    asyncio.run(main())
