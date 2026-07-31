# Observer — Comparación de Proveedores (telemetría real)

Fecha: 2026-07-30 · Fuente: `.manitos-state/observer_provider_metrics.sqlite3` · Tabla `provider_turns`
Muestra: 1384 turnos (ventana 07-26 16:04 → 07-30 22:09)

## Resumen

| modelo | n | avg duration (ms) | p95 (ms) | avg TTFT (ms) | degraded | truncated | errores | % err |
|---|---|---|---|---|---|---|---|---|
| unknown | 568 | 184 357 | 214 | 184 338 | 0 | 0 | 0 | 0% |
| phi4-mini | 218 | 47 | 93 | 50 | 0 | 0 | 21 | 10% |
| groq-1 | 80 | 136 | 716 | 1 | 0 | 0 | 0 | 0% |
| integration-smoke | 52 | 0.1 | 0.1 | 0.0 | 0 | 0 | 0 | 0% |
| llama-3.3-70b-groq | 34 | 15 423 | 47 692 | 8 235 | 8 | 3 | 0 | 0% |
| groq-llama3-70b | 20 | 5 | 11 | 5 | 0 | 0 | 0 | 0% |
| gemma3-4b | 18 | 95 | 122 | 77 | 0 | 0 | **18** | **100%** |
| qwen3-32b-groq | 8 | 7 577 | 9 813 | 4 780 | 0 | 0 | 0 | 0% |
| durability-check | 3 | 0.1 | 0.1 | 0.0 | 0 | 0 | 0 | 0% |

(Total excluido como contaminación: 383 filas `MagicMock` — ver abajo.)

## Hallazgos

1. **`gemma3-4b` falla 18/18 (100%).** El fallback local nunca completa un turno con éxito en este entorno. Urgente para la política de fallback del runtime.
2. **`phi4-mini` 10% de error** (21/218). El modelo local por defecto falla 1 de cada 10 turnos; correlacionar con `tool_error`/`truncated` no explica los 21 errores (0 degraded/truncated registrados).
3. **`unknown` 568 turnos con avg de 184 s** pero p95 de 214 ms → pocos outliers gigantes (>200 s) dominan el promedio. Perfil: turnos sin `model_id` capturado (probablemente el runtime no lo registra en algunos caminos) + un puñado de turnos colgados. Requiere corregir la captura de `model_id` y revisar los turnos que exceden 200 s.
4. **`groq-llama3-70b` avg 5.3 ms** — físicamente imposible para inferencia real; sospechoso de medir el camino de error (respuesta vacía) en vez del turno real. Revisar cómo se registra `duration_ms` en el adaptador cloud.
5. **`llama-3.3-70b-groq`** es el más lento (avg 15.4 s, p95 47.7 s) con 8 degraded y 3 truncated — coherente con un backend remoto bajo carga.
6. **Contaminación de tests (383/1384 = 28%).** Filas con `model` = `<MagicMock name='mock.model_id' id='...'>` y `<MagicMock name='mock.active_model().get()' id='...'>` escriben en la misma DB de producción que el runtime. Los tests que ejercen el pipeline de métricas deben aislar la ruta de `provider_turns` (p. ej. `MANITOS_OBSERVER_PROVIDER_METRICS_PATH` a un archivo temporal por test) para no ensuciar la telemetría real.

## Método

- SQL: `SELECT model, duration_ms, ttft_ms, degraded, truncated, tool_error, is_error, recorded_at FROM provider_turns`.
- Excluidas las filas cuyo `model` contiene `MagicMock` o `mock.`.
- p95 por modelo = percentil 95 de `duration_ms`.
- `integration-smoke` y `durability-check` son filas de verificaciones del lane (no producción).
