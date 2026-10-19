"""Tests for Python SDK instrumentors and exporters."""

from unittest.mock import MagicMock, patch

from llm_observatory.tracer import Tracer


class TestInstrumentFunction:
    """Tests for the instrument() dispatcher."""

    def test_instrument_openai(self):
        """Test that instrument(openai=True) calls OpenAI instrumentor."""
        with patch("llm_observatory.instrumentors.openai.OpenAIInstrumentor") as mock_cls:
            mock_inst = MagicMock()
            mock_cls.return_value = mock_inst

            from llm_observatory.instrumentors import instrument
            instrument(openai=True)

            mock_cls.assert_called_once()
            mock_inst.instrument.assert_called_once()

    def test_instrument_anthropic(self):
        """Test that instrument(anthropic=True) calls Anthropic instrumentor."""
        with patch("llm_observatory.instrumentors.anthropic.AnthropicInstrumentor") as mock_cls:
            mock_inst = MagicMock()
            mock_cls.return_value = mock_inst

            from llm_observatory.instrumentors import instrument
            instrument(anthropic=True)

            mock_cls.assert_called_once()
            mock_inst.instrument.assert_called_once()

    def test_instrument_nothing(self):
        """Test that instrument() with no flags does nothing."""
        from llm_observatory.instrumentors import instrument
        # Should not raise
        instrument()

    def test_uninstrument(self):
        """Test uninstrument clears active instrumentors."""
        from llm_observatory.instrumentors import _active_instrumentors, instrument, uninstrument
        _active_instrumentors.clear()

        with patch("llm_observatory.instrumentors.openai.OpenAIInstrumentor") as mock_cls:
            mock_inst = MagicMock()
            mock_cls.return_value = mock_inst

            instrument(openai=True)
            assert len(_active_instrumentors) == 1

            uninstrument()
            assert len(_active_instrumentors) == 0
            mock_inst.uninstrument.assert_called_once()


class TestOTLPExporter:
    """Tests for the OTLP exporter."""

    def test_export_sends_to_batch_endpoint(self):
        """Test that export sends to /v1/traces/batch."""
        from llm_observatory.exporters.otlp import OTLPExporter

        exporter = OTLPExporter(endpoint="http://localhost:8000")

        with patch.object(exporter._client, "post") as mock_post:
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            spans = [{"id": "test-span", "trace_id": "test-trace"}]
            exporter.export(spans)

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "/v1/traces/batch" in call_args[0][0]
            assert call_args[1]["json"] == {"spans": spans}

    def test_export_with_api_key(self):
        """Test that export includes Authorization header."""
        from llm_observatory.exporters.otlp import OTLPExporter

        exporter = OTLPExporter(
            endpoint="http://localhost:8000",
            api_key="test-key",
        )

        with patch.object(exporter._client, "post") as mock_post:
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            exporter.export([{"id": "test"}])

            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["headers"]["Authorization"] == "Bearer test-key"

    def test_export_retries_on_failure(self):
        """Test that export retries on HTTP errors."""
        import httpx

        from llm_observatory.exporters.otlp import OTLPExporter

        exporter = OTLPExporter(endpoint="http://localhost:8000", max_retries=2)

        with patch.object(exporter._client, "post") as mock_post:
            # First call fails, second succeeds
            mock_response = MagicMock()
            mock_response.raise_for_status.side_effect = [
                httpx.HTTPStatusError(
                    "500",
                    request=MagicMock(),
                    response=MagicMock(status_code=500),
                ),
                None,
            ]
            mock_post.return_value = mock_response

            exporter.export([{"id": "test"}])
            assert mock_post.call_count == 2


class TestTracerWithExporter:
    """Tests for Tracer integration with exporters."""

    def test_add_exporter(self):
        """Test adding an exporter to tracer."""
        tracer = Tracer()
        mock_exporter = MagicMock()
        tracer.add_exporter(mock_exporter)
        assert mock_exporter in tracer._exporters

    def test_span_is_exported_when_it_ends_not_when_it_starts(self):
        """A span is queued at end() (evaluation C2): exporting it at creation sent no end time,
        no status and no tokens, and the later mutations never reached the backend."""
        tracer = Tracer(batch_size=1)
        mock_exporter = MagicMock()
        tracer.add_exporter(mock_exporter)

        span = tracer.start_span("test-span")
        tracer.flush()
        mock_exporter.export.assert_not_called()

        span.end()
        tracer.flush()
        mock_exporter.export.assert_called()
        call_args = mock_exporter.export.call_args[0][0]
        assert len(call_args) == 1
        assert call_args[0]["name"] == "test-span"

    def test_flush(self):
        """Test that flush sends buffered spans."""
        tracer = Tracer()
        mock_exporter = MagicMock()
        tracer.add_exporter(mock_exporter)

        tracer.start_span("span-1").end()
        tracer.start_span("span-2").end()

        # Buffer should have 2 finished spans (not yet flushed)
        assert len(tracer._buffer) == 2

        tracer.flush()

        # Buffer should be empty, exporter called
        assert len(tracer._buffer) == 0
        mock_exporter.export.assert_called()

    def test_export_format(self):
        """Test that export produces correct format."""
        tracer = Tracer()
        span = tracer.start_span("test")
        span.set_token_usage(100, 50)
        span.set_cost(0.001)

        exported = tracer.export()
        assert len(exported) == 1

        span_dict = exported[0]
        assert span_dict["name"] == "test"
        assert span_dict["tokens_input"] == 100
        assert span_dict["tokens_output"] == 50
        assert span_dict["cost_usd"] == 0.001
        # Should be flat, not nested
        assert "tokens" not in span_dict or span_dict.get("tokens") is None


class TestLangChainInstrumentor:
    """Tests for LangChain instrumentor."""

    def test_instrument_calls_patch(self):
        """Test instrument() monkey-patches BaseCallbackHandler."""
        from llm_observatory.instrumentors.langchain import LangChainInstrumentor

        instrumentor = LangChainInstrumentor()
        mock_handler_cls = MagicMock()
        fake_lc = MagicMock()
        mods = {"langchain_core": fake_lc, "langchain_core.callbacks": fake_lc.callbacks}
        with patch.dict("sys.modules", mods):
            import llm_observatory.instrumentors.langchain as mod
            with patch.object(mod, "BaseCallbackHandler", mock_handler_cls, create=True):
                instrumentor.instrument()
                assert instrumentor._patched is True
                assert mock_handler_cls.on_llm_start is not None

    def test_uninstrument_restores(self):
        """Test uninstrument() restores original methods."""
        from llm_observatory.instrumentors.langchain import LangChainInstrumentor

        instrumentor = LangChainInstrumentor()
        mock_handler_cls = MagicMock()
        fake_lc = MagicMock()
        mods = {"langchain_core": fake_lc, "langchain_core.callbacks": fake_lc.callbacks}
        with patch.dict("sys.modules", mods):
            import llm_observatory.instrumentors.langchain as mod
            with patch.object(mod, "BaseCallbackHandler", mock_handler_cls, create=True):
                original_start = mock_handler_cls.on_llm_start
                instrumentor.instrument()
                instrumentor.uninstrument()
                assert mock_handler_cls.on_llm_start is original_start

    def test_noop_when_not_importable(self):
        """Test instrument() is a no-op when langchain is not installed."""
        with patch.dict("sys.modules", {"langchain_core": None, "langchain_core.callbacks": None}):
            from llm_observatory.instrumentors.langchain import LangChainInstrumentor

            instrumentor = LangChainInstrumentor()
            # Should not raise
            instrumentor.instrument()
            assert instrumentor._patched is False
