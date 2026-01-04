"""Thread management utilities for proper cleanup on shutdown"""

import threading
import logging
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


class ThreadManager:
    """Manages long-running threads and ensures proper cleanup on shutdown.

    For short-lived background tasks (like fetching data, running commands),
    daemon threads are fine. But for long-running threads that manage resources
    (like connection pools, file handles, etc.), use this manager.

    Usage:
        manager = ThreadManager()
        manager.start_thread("my_worker", my_function, args=(arg1, arg2))

        # On shutdown
        manager.shutdown(timeout=5)
    """

    def __init__(self):
        self._threads: List[threading.Thread] = []
        self._stop_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def start_thread(
        self,
        name: str,
        target: Callable,
        args: tuple = (),
        kwargs: dict = None,
        stop_event: threading.Event = None
    ) -> threading.Thread:
        """Start a managed thread.

        Args:
            name: Thread name for identification
            target: Function to run in thread
            args: Positional arguments for target
            kwargs: Keyword arguments for target
            stop_event: Optional event to signal thread to stop

        Returns:
            The started thread
        """
        if kwargs is None:
            kwargs = {}

        thread = threading.Thread(target=target, args=args, kwargs=kwargs, name=name)
        thread.daemon = False  # Non-daemon so we can clean up properly

        with self._lock:
            self._threads.append(thread)
            if stop_event:
                self._stop_events[name] = stop_event

        thread.start()
        logger.debug(f"Started managed thread: {name}")
        return thread

    def stop_thread(self, name: str, timeout: float = 5.0) -> bool:
        """Stop a specific thread by name.

        Args:
            name: Thread name to stop
            timeout: Seconds to wait for thread to join

        Returns:
            True if thread stopped, False if still running
        """
        with self._lock:
            # Signal stop if we have an event
            if name in self._stop_events:
                self._stop_events[name].set()
                logger.debug(f"Signaled stop for thread: {name}")

            # Find and join the thread
            for thread in self._threads:
                if thread.name == name:
                    thread.join(timeout=timeout)
                    if thread.is_alive():
                        logger.warning(f"Thread {name} did not stop within {timeout}s")
                        return False
                    else:
                        self._threads.remove(thread)
                        if name in self._stop_events:
                            del self._stop_events[name]
                        logger.debug(f"Thread {name} stopped")
                        return True

        logger.warning(f"Thread {name} not found")
        return False

    def shutdown(self, timeout: float = 5.0) -> int:
        """Stop all managed threads.

        Args:
            timeout: Seconds to wait for each thread

        Returns:
            Number of threads that didn't stop in time
        """
        logger.info(f"Shutting down {len(self._threads)} managed threads...")

        # Signal all stop events first
        with self._lock:
            for name, event in self._stop_events.items():
                event.set()
                logger.debug(f"Signaled stop for: {name}")

        # Wait for threads to finish
        still_running = 0
        with self._lock:
            for thread in self._threads[:]:  # Copy list since we modify it
                thread.join(timeout=timeout)
                if thread.is_alive():
                    logger.warning(f"Thread {thread.name} still running after shutdown")
                    still_running += 1
                else:
                    self._threads.remove(thread)
                    logger.debug(f"Thread {thread.name} stopped")

        if still_running:
            logger.warning(f"{still_running} threads still running after shutdown")
        else:
            logger.info("All managed threads stopped")

        return still_running

    @property
    def running_threads(self) -> List[str]:
        """Get names of currently running threads."""
        with self._lock:
            return [t.name for t in self._threads if t.is_alive()]


# Global instance for app-wide thread management
_global_manager: Optional[ThreadManager] = None


def get_thread_manager() -> ThreadManager:
    """Get the global thread manager instance."""
    global _global_manager
    if _global_manager is None:
        _global_manager = ThreadManager()
    return _global_manager


def shutdown_all_threads(timeout: float = 5.0) -> int:
    """Convenience function to shutdown all globally managed threads."""
    global _global_manager
    if _global_manager is not None:
        return _global_manager.shutdown(timeout)
    return 0
