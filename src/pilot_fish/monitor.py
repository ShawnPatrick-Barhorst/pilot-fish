import os
import sys
import sysconfig
import threading
import time
from itertools import count
from types import CodeType, FunctionType, MethodType

from .ring_buffer import SPSCRingBuffer

from .probe import Tap, TapMethod

from .registry import type_registry

TOOL_ID = sys.monitoring.PROFILER_ID
EVENTS = sys.monitoring.events

PY_START = type_registry.register_type("py_start")
PY_RETURN = type_registry.register_type("py_return")
PY_UNWIND = type_registry.register_type("py_unwind")
PY_YIELD = type_registry.register_type("py_yield")
PY_RESUME = type_registry.register_type("py_resume")
PY_THROW = type_registry.register_type("py_throw")
C_CALL = type_registry.register_type("c_call")
C_RETURN = type_registry.register_type("c_return")
C_RAISE = type_registry.register_type("c_raise")

_PYTHON_CALLABLE_TYPES = frozenset({FunctionType, MethodType, TapMethod})


def _library_prefixes() -> tuple[str, ...]:
    install_paths = sysconfig.get_paths()
    prefixes = set()
    for path_name in ("stdlib", "platstdlib", "purelib", "platlib"):
        path = install_paths.get(path_name)
        if path:
            prefixes.add(path.rstrip(os.sep) + os.sep)
            prefixes.add(os.path.realpath(path).rstrip(os.sep) + os.sep)
    return tuple(prefixes)

_LIBRARY_PREFIXES = _library_prefixes()


def _code_objects_of(cls) -> set[CodeType]:
    return {
        member.__code__
        for base_class in cls.__mro__
        for member in vars(base_class).values()
        if isinstance(member, FunctionType)
    }

class ThreadState:

    __slots__ = ("call_stack", "drained", "push", "ring", "thread")

    def __init__(self, ring_capacity: int):
        self.call_stack: list[tuple[int, CodeType, int | None]] = []
        self.ring: SPSCRingBuffer = SPSCRingBuffer(ring_capacity)
        self.push = self.ring.push  # the method itself, not a call to it
        self.thread = threading.current_thread()
        self.drained = threading.Event()

class Engine:

    def __init__(self, monitor, sink, polling_interval=0.1):
        self.monitor = monitor
        self.sink = sink
        self.polling_interval = polling_interval

        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name='pilot-fish', daemon=True)

        self._code_ids: dict[CodeType, int] = {}

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._thread.join()
        self._drain_all()
        self.sink.flush()

    def _run(self):
        self.monitor._thread_local.thread_state = None
        while not self._stop_event.is_set():
            self._drain_all()
            self._stop_event.wait(self.polling_interval)

    def _write_batch(self, thread_state: ThreadState, rows: list):
        newly_seen_functions = []
        encoded_rows = []
        for kind, call_id, parent_id, code_object, bytecode_offset, timestamp, extra in rows:
            code_id = self._code_ids.get(code_object)
            if code_id is None:
                code_id = self._code_ids[code_object] = len(self._code_ids) + 1
                newly_seen_functions.append(
                    (
                        code_id,
                        code_object.co_qualname,
                        code_object.co_filename,
                        code_object.co_firstlineno,
                    )
                )
            encoded_rows.append((kind, call_id, parent_id, code_id, bytecode_offset, timestamp, extra))

        if newly_seen_functions:
            self.sink.write_batch(("codes", newly_seen_functions))
        self.sink.write_batch(("events", thread_state.thread.name, encoded_rows))


    def _drain_all(self):
        with self.monitor._thread_states_lock:
            thread_states = list(self.monitor._thread_states)
        for thread_state in thread_states:
            rows = thread_state.ring.drain()
            if rows:
                self._write_batch(thread_state, rows)


class Monitor:

    def __init__(self, sink, tap: Tap | None = None, ring_capacity: int = 1 << 18):
        self.ring_capacity = ring_capacity

        self._thread_local = threading.local()
        self._thread_states: list[ThreadState] = []
        self._thread_states_lock = threading.Lock()
        self._is_user_code_cache: dict[CodeType, bool] = {}
        self._call_id_counter = count(1)
        self._suspended_generator_calls: dict[int, tuple] = {}
        self._callbacks: dict[int, object] = {}

        self.tap = tap if tap is not None else Tap()

        self._events = (
            EVENTS.PY_START
            | EVENTS.PY_RETURN
            | EVENTS.PY_UNWIND
            | EVENTS.PY_YIELD
            | EVENTS.PY_RESUME
            | EVENTS.PY_THROW
            | EVENTS.CALL
        )

        self._excluded_code_objects = (
            _code_objects_of(Monitor)
            | _code_objects_of(ThreadState)
            | _code_objects_of(Engine)
            | _code_objects_of(SPSCRingBuffer)
            | _code_objects_of(type(sink))
            | _code_objects_of(Tap)
            | _code_objects_of(TapMethod)
            | self.tap.extension_code_objects
        )
        self.engine = Engine(self, sink)

    def _is_user_code(self, code_object: CodeType) -> bool:

        cached_answer = self._is_user_code_cache.get(code_object)
        if cached_answer is not None:
            return cached_answer

        filename = code_object.co_filename
        if code_object in self._excluded_code_objects:
            is_user_code = False
        elif filename.startswith("<"):
            is_user_code = filename.startswith("<ipython-input")
        elif (
            "site-packages" in filename
            or "dist-packages" in filename
            or filename.startswith(_LIBRARY_PREFIXES)
        ):
            is_user_code = False
        else:
            is_user_code = True

        self._is_user_code_cache[code_object] = is_user_code
        return is_user_code

    def _get_or_create_thread_state(self) -> ThreadState:

        try:
            return self._thread_local.thread_state
        except AttributeError:
            thread_state = ThreadState(self.ring_capacity)
            self._thread_local.thread_state = thread_state
            with self._thread_states_lock:
                self._thread_states.append(thread_state)
            return thread_state

    def _build_callbacks(self) -> dict[int, object]:
        thread_local = self._thread_local
        cached_is_user_code = self._is_user_code_cache.get
        is_user_code = self._is_user_code
        get_or_create_thread_state = self._get_or_create_thread_state
        next_call_id = self._call_id_counter.__next__
        suspended_generator_calls = self._suspended_generator_calls
        current_time_ns = time.perf_counter_ns
        get_frame = sys._getframe
        python_callable_types = _PYTHON_CALLABLE_TYPES
        DISABLE = sys.monitoring.DISABLE

        def on_py_start(code_object, bytecode_offset):

            is_user = cached_is_user_code(code_object)
            if is_user is None:
                is_user = is_user_code(code_object)
            if not is_user:
                return DISABLE

            try:
                thread_state = thread_local.thread_state
            except AttributeError:
                thread_state = get_or_create_thread_state()
            if thread_state is None:
                return

            call_stack = thread_state.call_stack
            call_id = next_call_id()
            parent_id = call_stack[-1][0] if call_stack else None
            call_stack.append((call_id, code_object, None))

            timestamp = current_time_ns()
            thread_state.push((PY_START, call_id, parent_id, code_object, bytecode_offset, timestamp, None))

        def on_py_return(code_object, bytecode_offset, return_value):

            timestamp = current_time_ns()

            try:
                thread_state = thread_local.thread_state
            except AttributeError:
                thread_state = None
            if thread_state is not None:
                call_stack = thread_state.call_stack
                if call_stack and call_stack[-1][1] is code_object:
                    call_id = call_stack.pop()[0]
                    thread_state.push((PY_RETURN, call_id, None, code_object, bytecode_offset, timestamp, None))
                    return

            is_user = cached_is_user_code(code_object)
            if is_user is None:
                is_user = is_user_code(code_object)
            if not is_user:
                return DISABLE

        def on_py_unwind(code_object, bytecode_offset, exception):

            timestamp = current_time_ns()

            try:
                thread_state = thread_local.thread_state
            except AttributeError:
                return
            if thread_state is None:
                return

            call_stack = thread_state.call_stack
            if call_stack and call_stack[-1][1] is code_object:
                call_id = call_stack.pop()[0]
                exception_name = type(exception).__qualname__
                thread_state.push((PY_UNWIND, call_id, None, code_object, bytecode_offset, timestamp, exception_name))

        def on_py_yield(code_object, bytecode_offset, yielded_value):

            timestamp = current_time_ns()

            try:
                thread_state = thread_local.thread_state
            except AttributeError:
                thread_state = None
            if thread_state is not None:
                call_stack = thread_state.call_stack
                if call_stack and call_stack[-1][1] is code_object:
                    suspended_call = call_stack.pop()
                    generator_frame = get_frame(1)
                    suspended_generator_calls[id(generator_frame)] = suspended_call
                    thread_state.push((PY_YIELD, suspended_call[0], None, code_object, bytecode_offset, timestamp, None))
                    return

            is_user = cached_is_user_code(code_object)
            if is_user is None:
                is_user = is_user_code(code_object)
            if not is_user:
                return DISABLE

        def resume_generator_call(kind, code_object, bytecode_offset, extra):

            generator_frame = get_frame(2)
            suspended_call = suspended_generator_calls.pop(id(generator_frame), None)
            if suspended_call is None:
                return  # the generator started before monitoring began

            try:
                thread_state = thread_local.thread_state
            except AttributeError:
                thread_state = get_or_create_thread_state()
            if thread_state is None:
                return

            thread_state.call_stack.append(suspended_call)
            timestamp = current_time_ns()
            thread_state.push((kind, suspended_call[0], None, code_object, bytecode_offset, timestamp, extra))

        def on_py_resume(code_object, bytecode_offset):

            is_user = cached_is_user_code(code_object)
            if is_user is None:
                is_user = is_user_code(code_object)
            if not is_user:
                return DISABLE

            resume_generator_call(PY_RESUME, code_object, bytecode_offset, None)

        def on_py_throw(code_object, bytecode_offset, exception):

            if cached_is_user_code(code_object):
                exception_name = type(exception).__qualname__
                resume_generator_call(PY_THROW, code_object, bytecode_offset, exception_name)

        def on_call(code_object, bytecode_offset, called_object, first_argument):

            is_user = cached_is_user_code(code_object)
            if is_user is None:
                is_user = is_user_code(code_object)
            if not is_user:
                return DISABLE  # also silences C_RETURN and C_RAISE at this call site

            if type(called_object) in python_callable_types:
                return  # PY_START records Python calls; can't DISABLE, the call site may call C later

            try:
                thread_state = thread_local.thread_state
            except AttributeError:
                thread_state = get_or_create_thread_state()
            if thread_state is None:
                return

            call_stack = thread_state.call_stack
            call_id = next_call_id()
            parent_id = call_stack[-1][0] if call_stack else None
            call_stack.append((call_id, code_object, bytecode_offset))

            called_name = (
                getattr(called_object, "__qualname__", None)
                or getattr(called_object, "__name__", None)
                or type(called_object).__qualname__
            )
            called_module = getattr(called_object, "__module__", None)
            timestamp = current_time_ns()
            thread_state.push((C_CALL, call_id, parent_id, code_object, bytecode_offset, timestamp, (called_name, called_module)))

        def make_on_c_call_end(kind):
            
            def on_c_call_end(code_object, bytecode_offset, called_object, first_argument):

                timestamp = current_time_ns()

                try:
                    thread_state = thread_local.thread_state
                except AttributeError:
                    return
                if thread_state is None:
                    return

                call_stack = thread_state.call_stack
                if call_stack:
                    call_id, call_code_object, call_site_offset = call_stack[-1]
                    if call_code_object is code_object and call_site_offset == bytecode_offset:
                        call_stack.pop()
                        thread_state.push((kind, call_id, None, code_object, bytecode_offset, timestamp, None))

            return on_c_call_end

        callbacks = {
            EVENTS.PY_START: on_py_start,
            EVENTS.PY_RETURN: on_py_return,
            EVENTS.PY_UNWIND: on_py_unwind,
            EVENTS.PY_YIELD: on_py_yield,
            EVENTS.PY_RESUME: on_py_resume,
            EVENTS.PY_THROW: on_py_throw,
            EVENTS.CALL: on_call,
            EVENTS.C_RETURN: make_on_c_call_end(C_RETURN),
            EVENTS.C_RAISE: make_on_c_call_end(C_RAISE)
        }
        return callbacks

    def __enter__(self):        
        current_tool_name = sys.monitoring.get_tool(TOOL_ID)
        if current_tool_name is None:
            sys.monitoring.use_tool_id(TOOL_ID, "pytap")
        elif current_tool_name != "pytap":
            raise RuntimeError(f"tool id {TOOL_ID} is already used by {current_tool_name!r}")

        self._callbacks = self._build_callbacks()
        for event, callback in self._callbacks.items():
            sys.monitoring.register_callback(TOOL_ID, event, callback)
        sys.monitoring.restart_events()
        self.tap.monitor = self
        sys.monitoring.set_events(TOOL_ID, self._events)
        self.engine.start()
        return self

    def __exit__(self, exception_type, exception, traceback):
        sys.monitoring.set_events(TOOL_ID, EVENTS.NO_EVENTS)
        self.tap.monitor = None
        for event in self._callbacks:
            sys.monitoring.register_callback(TOOL_ID, event, None)
        sys.monitoring.free_tool_id(TOOL_ID)

        self.engine.stop()
        return False
