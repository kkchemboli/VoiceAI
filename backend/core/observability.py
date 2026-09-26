"""Dependency-free observability primitives shared by service runtimes."""

import contextvars
import json
import logging
import os
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Optional

request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
call_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("call_id", default="-")
room_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("room_id", default="-")
task_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("task_id", default="-")
service_context: contextvars.ContextVar[str] = contextvars.ContextVar("service", default="unknown")

otel_meter = None


def current_otel_context() -> tuple[str, str]:
    """Return (trace_id, span_id) hex strings from the active OTel span, or ("-", "-")."""
    try:
        from opentelemetry import trace
    except ImportError:
        return "-", "-"
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        return f"{span_context.trace_id:032x}", f"{span_context.span_id:016x}"
    return "-", "-"


class CorrelationFilter(logging.Filter):
    """Synchronize the active OTel span context into every emitted log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.otel_trace_id, record.otel_span_id = current_otel_context()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": service_context.get(),
            "message": record.getMessage(),
            "request_id": request_id_context.get(),
            "call_id": call_id_context.get(),
            "room_id": room_id_context.get(),
            "task_id": getattr(record, "task_id", task_id_context.get()),
            "trace_id": getattr(record, "otel_trace_id", "-"),
            "span_id": getattr(record, "otel_span_id", "-"),
        }
        for field in ("method", "path", "status_code", "duration_ms"):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(service_name: str) -> None:
    service_context.set(service_name)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(CorrelationFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


class Metrics:
    """Process-local metrics mirrored to OpenTelemetry when configured."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}
        self._otel_counters: dict[str, Any] = {}
        self._otel_gauges: dict[str, Any] = {}
        self._otel_histograms: dict[str, Any] = {}

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount
            if otel_meter is not None:
                counter = self._otel_counters.get(name)
                if counter is None:
                    counter = otel_meter.create_counter(
                        name,
                        description=f"Count of {name}",
                    )
                    self._otel_counters[name] = counter
                counter.add(amount)

    def record(self, name: str, value: float) -> None:
        """Record an instantaneous value (gauge)."""
        with self._lock:
            self._gauges[name] = value
            if otel_meter is not None and name not in self._otel_gauges:
                gauge = otel_meter.create_observable_gauge(
                    name,
                    description=f"Current value of {name}",
                    callbacks=[self._gauge_callback(name)],
                )
                self._otel_gauges[name] = gauge

    def record_duration(self, name: str, value_ms: float) -> None:
        """Record a latency sample (histogram)."""
        with self._lock:
            samples = self._histograms.get(name, [])
            samples.append(value_ms)
            self._histograms[name] = samples[-1000:]
            if otel_meter is not None:
                histogram = self._otel_histograms.get(name)
                if histogram is None:
                    histogram = otel_meter.create_histogram(
                        name,
                        description=f"Duration of {name}",
                        unit="ms",
                    )
                    self._otel_histograms[name] = histogram
                histogram.record(value_ms)

    def _gauge_callback(self, name: str) -> Callable[[Any], Any]:
        def callback(callback_options: Any) -> Any:
            from opentelemetry.metrics import Measurement
            with self._lock:
                return [Measurement(self._gauges.get(name, 0.0), {})]
        return callback

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {k: v[-10:] for k, v in self._histograms.items()},
            }


metrics = Metrics()


def configure_otel(service_name: str) -> None:
    """Enable OTLP logs and metrics when an endpoint is configured.

    Trace export is deliberately configured by the tracing backend integration,
    rather than this shared runtime setup.  This keeps metrics/log export usable
    when no trace backend is deployed.
    """
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return
    try:
        from opentelemetry import metrics as otel_metrics
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        resource = Resource.create({"service.name": service_name})
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=endpoint, insecure=True)
                )
            ],
        )
        global otel_meter
        otel_metrics.set_meter_provider(meter_provider)
        otel_meter = otel_metrics.get_meter(service_name)

        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint, insecure=True))
        )
        logging.getLogger().addHandler(
            LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
        )
        logging.getLogger("observability").info(
            "OpenTelemetry export enabled",
            extra={"otel_endpoint": endpoint, "service_name": service_name},
        )
    except ImportError:
        logging.getLogger("observability").warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT is set but OpenTelemetry packages are unavailable"
        )
    install_instrumentation()


def configure_otel_tracing(service_name: str) -> Any:
    """Configure an OTLP trace provider only for a service that opts in.

    The collector performs tail-based sampling, so the SDK must create every
    span and let the collector decide which complete traces to retain.
    """
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    enabled = os.getenv("OTEL_TRACES_ENABLED", "false").lower() in {"1", "true", "yes"}
    if not endpoint or not enabled:
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
        )
        trace.set_tracer_provider(provider)
        logging.getLogger("observability").info(
            "OpenTelemetry trace export enabled",
            extra={"otel_endpoint": endpoint, "service_name": service_name},
        )
        return provider
    except ImportError:
        logging.getLogger("observability").warning(
            "OTEL_TRACES_ENABLED is set but OpenTelemetry trace packages are unavailable"
        )
        return None


def install_instrumentation() -> None:
    """Apply optional OpenTelemetry auto-instrumentation for the I/O libraries in use."""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except ImportError:
        pass
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        RequestsInstrumentor().instrument()
    except ImportError:
        pass
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        RedisInstrumentor().instrument()
    except ImportError:
        pass
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        CeleryInstrumentor().instrument()
    except ImportError:
        pass


def init_sentry(service_name: str) -> None:
    """Initialize Sentry only when a DSN is provided so local runs stay silent."""
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            environment=os.getenv("ENVIRONMENT", "production"),
            release=os.getenv("SERVICE_VERSION", "dev"),
            service_name=service_name,
        )
    except ImportError:
        pass


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def set_correlation(
    *,
    request_id: Optional[str] = None,
    call_id: Optional[str] = None,
    room_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> None:
    """Set business correlation identifiers for the current execution context."""
    if request_id is not None:
        request_id_context.set(request_id)
    if call_id is not None:
        call_id_context.set(call_id)
    if room_id is not None:
        room_id_context.set(room_id)
    if task_id is not None:
        task_id_context.set(task_id)


@contextmanager
def correlation_context(
    *,
    request_id: Optional[str] = None,
    call_id: Optional[str] = None,
    room_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Iterator[None]:
    """Scoped business correlation identifiers, restored on exit."""
    tokens: list[tuple[contextvars.ContextVar, contextvars.Token]] = []
    for var, value in (
        (request_id_context, request_id),
        (call_id_context, call_id),
        (room_id_context, room_id),
        (task_id_context, task_id),
    ):
        if value is not None:
            tokens.append((var, var.set(value)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def current_traceparent() -> Optional[str]:
    """Inject the W3C traceparent for the active OTel span, if any."""
    try:
        from opentelemetry import trace
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    except ImportError:
        return None
    if not trace.get_current_span().get_span_context().is_valid:
        return None
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    return carrier.get("traceparent")


def extract_otel_context(headers: Optional[dict]) -> Any:
    """Build an OTel parent context from a W3C traceparent carried in `headers`."""
    try:
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    except ImportError:
        return None
    carrier = {
        "traceparent": (headers or {}).get("traceparent", ""),
        "tracestate": (headers or {}).get("tracestate", ""),
    }
    if not carrier["traceparent"]:
        return None
    return TraceContextTextMapPropagator().extract(carrier)


def attach_otel_context(context: Any) -> Any:
    """Attach a parent OTel context to the current execution context."""
    try:
        from opentelemetry import context as otel_context
    except ImportError:
        return None
    if context is None:
        return None
    return otel_context.attach(context)


def detach_otel_context(token: Any) -> None:
    if token is None:
        return
    try:
        from opentelemetry import context as otel_context
    except ImportError:
        return
    otel_context.detach(token)


def celery_task_headers(
    *,
    call_id: Optional[str] = None,
    room_id: Optional[str] = None,
) -> dict[str, str]:
    """Headers to propagate business correlation + W3C trace context into a Celery task."""
    headers: dict[str, str] = {}
    if call_id:
        headers["call_id"] = call_id
    if room_id:
        headers["room_id"] = room_id
    traceparent = current_traceparent()
    if traceparent:
        headers["traceparent"] = traceparent
    return headers


@contextmanager
def start_span(
    name: str,
    attributes: Optional[dict] = None,
    context: Any = None,
    kind: Any = None,
) -> Iterator[Any]:
    """Create an OTel span when configured; otherwise behave as a no-op context."""
    try:
        from opentelemetry import trace
    except ImportError:
        yield None
        return

    tracer = trace.get_tracer(service_context.get())
    kwargs: dict[str, Any] = {}
    if context is not None:
        kwargs["context"] = context
    if kind is not None:
        kwargs["kind"] = kind
    with tracer.start_as_current_span(name, **kwargs) as span:
        for key, value in (attributes or {}).items():
            span.set_attribute(key, value)
        yield span


def observe_request(method: str, path: str, status_code: int, duration_ms: float) -> None:
    metrics.increment("http_requests_total")
    metrics.increment(f"http_requests_{status_code // 100}xx_total")
    metrics.record_duration("http_request_duration_ms", duration_ms)
    logging.getLogger("http").info(
        "HTTP request completed",
        extra={
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": round(duration_ms, 2),
        },
    )


def start_health_server(port: int = 9009) -> None:
    """Expose a minimal /health/live endpoint for runtimes without an HTTP server."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/health/live"):
                body = b'{"status":"ok"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, fmt: str, *args) -> None:  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
