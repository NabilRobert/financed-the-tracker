"""Spans for the webhook path, following agent-observability's conventions (ADR-015).

Off unless an endpoint is set. Spans hold names, timings and outcomes, never content: no
message text, amounts, names or numbers. So there's no auto-instrumentation, which would
record SQL and URLs, and an exception adds only its class as `error.type`, because its
message can quote the input.
"""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Final

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

SERVICE_NAME: Final = "whatsapp-finance-tracker"

_tracer: Final = trace.get_tracer("tracker")


def create_tracer_provider(endpoint: str, version: str) -> TracerProvider:
    resource = Resource.create({"service.name": SERVICE_NAME, "service.version": version})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    return provider


def configure_tracing(endpoint: str | None, version: str) -> TracerProvider | None:
    """Sends spans to `endpoint`. Without one the tracer does nothing, and nothing leaves the app."""
    if endpoint is None:
        return None
    provider = create_tracer_provider(endpoint, version)
    trace.set_tracer_provider(provider)
    return provider


@contextmanager
def traced(name: str, kind: SpanKind = SpanKind.INTERNAL) -> Generator[Span]:
    """A span with no content, even on failure: the exception's text stays out of it."""
    with _tracer.start_as_current_span(name, kind=kind, record_exception=False, set_status_on_exception=False) as span:
        try:
            yield span
        except Exception as error:
            span.set_attribute("error.type", type(error).__name__)
            span.set_status(Status(StatusCode.ERROR))
            raise
