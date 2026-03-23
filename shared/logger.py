"""Shared logging utilities for Selanet examples."""

import asyncio
import time


class Logger:
    """Simple dual-output logger (stdout + file)."""

    def __init__(self, log_file=None):
        self._file = open(log_file, "w", encoding="utf-8") if log_file else None

    def log(self, msg, end="\n", flush=True):
        print(msg, end=end, flush=flush)
        if self._file:
            self._file.write(msg + end)
            if flush:
                self._file.flush()

    def close(self):
        if self._file:
            self._file.close()
            self._file = None


def create_logger(log_file=None):
    """Create a Logger instance."""
    return Logger(log_file)


async def timed(logger, label, coro, timeout=300):
    """Run a coroutine with timeout, timing, and logging.

    Returns:
        (result, error): result on success, error string on failure.
    """
    logger.log(f"  [{time.strftime('%H:%M:%S')}] {label}...", end=" ")
    t0 = time.time()
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
        elapsed = time.time() - t0
        logger.log(f"OK ({elapsed:.1f}s)")
        return result, None
    except asyncio.TimeoutError:
        logger.log(f"TIMEOUT after {timeout}s")
        return None, "timeout"
    except Exception as e:
        elapsed = time.time() - t0
        err_msg = f"{type(e).__name__}: {e}"
        logger.log(f"ERROR ({elapsed:.1f}s): {err_msg}")
        return None, err_msg
