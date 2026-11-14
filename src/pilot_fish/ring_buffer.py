import threading
from typing import Any


class SPSCRingBuffer:
    def __init__(self, capacity):

        if capacity <= 0 or capacity & (capacity - 1) != 0:
            raise ValueError("Capacity must be a power of 2")

        self._buffer = [None] * capacity
        self._capacity = capacity
        self._mask = capacity - 1

        self._head = 0
        self._tail = 0

        self._head_fence = threading.Lock()
        self._tail_fence = threading.Lock()

    def push(self, item: Any) -> bool:

        current_tail = self._tail

        # Buffer is full of unread values
        if self._head - current_tail >= self._capacity:
            return False

        head_index = self._head & self._mask
        self._buffer[head_index] = item

        self._head += 1

        return True

    def pop(self) -> Any | None:

        current_head = self._head

        # Buffer is empty, no values to read
        if current_head - self._tail == 0:
            return None

        tail_index = self._tail & self._mask
        item = self._buffer[tail_index]
        self._buffer[tail_index] = None

        self._tail += 1

        return item


    def drain(self) -> list:
        
        head = self._head
        tail = self._tail
        buf, mask = self._buffer, self._mask
        items = [buf[i & mask] for i in range(tail, head)]
        self._tail = head
        return items