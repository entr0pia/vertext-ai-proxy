# Spec Delta

## MODIFIED Requirements

### Requirement: Embeddings Proxying and Schema Translation
The proxy SHALL expose an OpenAI-compatible `POST /v1/embeddings` endpoint that validates authentication, accepts standard OpenAI embedding payloads, routes requests to the appropriate upstream Vertex AI protocol (either `:predict` for traditional models or `:embedContent`/`:batchEmbedContents` for Gemini multimodal models), and returns embeddings in standard OpenAI format.

#### Scenario: Single string input embedding generation
- **WHEN** an authorized client sends a POST request to `/v1/embeddings` with `"input": "text to embed"` and a traditional model name (e.g. `text-embedding-004`)
- **THEN** the proxy transforms the input into an instance object `{"content": "text to embed"}`, calls the Vertex AI native prediction endpoint (`:predict`), and returns an OpenAI-compatible embedding response with `data[0].embedding` and usage statistics

#### Scenario: Multiple strings batch input embedding generation
- **WHEN** an authorized client sends a POST request to `/v1/embeddings` with `"input": ["text 1", "text 2"]` and a traditional model name
- **THEN** the proxy transforms each item into a separate prediction instance, queries the Vertex AI prediction endpoint (`:predict`), and returns each embedding preserving its original index

#### Scenario: Optional dimensions parameter handling
- **WHEN** an authorized client provides an optional `"dimensions": 512` parameter in the request payload for traditional models
- **THEN** the proxy passes `"parameters": {"outputDimensionality": 512}` to the Vertex AI prediction endpoint

#### Scenario: Model identifier normalization for embeddings
- **WHEN** an authorized client requests an embedding with an unprefixed model identifier (such as `text-embedding-004`, `gemini-embedding-001`, or `gemini-embedding-2`)
- **THEN** the proxy correctly maps the model name to the Vertex AI publisher model resource path

#### Scenario: Empty or invalid input handling
- **WHEN** an authorized client sends an empty string or empty array in `"input"`
- **THEN** the proxy rejects the request immediately with an HTTP 400 Bad Request and an OpenAI-compatible error payload

#### Scenario: Gemini multimodal embedding single string generation
- **WHEN** an authorized client sends a POST request to `/v1/embeddings` with `"input": "text to embed"` and model `gemini-embedding-2` (or `google/gemini-embedding-2`)
- **THEN** the proxy translates the payload into `{"content": {"parts": [{"text": "text to embed"}]}}`, sends a POST request to the `:embedContent` endpoint, and normalizes the returned `embedding.values` into an OpenAI-compatible embedding object at index 0

#### Scenario: Gemini multimodal embedding batch strings generation
- **WHEN** an authorized client sends a POST request to `/v1/embeddings` with `"input": ["doc1", "doc2"]` and model `gemini-embedding-2`
- **THEN** the proxy translates the payload into `{"requests": [{"model": "...", "content": {"parts": [{"text": "doc1"}]}}, {"model": "...", "content": {"parts": [{"text": "doc2"}]}}]}`, sends a POST request to `:batchEmbedContents`, and normalizes the returned array of embeddings preserving original input order

#### Scenario: Gemini embedding dimension scaling
- **WHEN** an authorized client requests embeddings for `gemini-embedding-2` with `"dimensions": 768`
- **THEN** the proxy sets `outputDimensionality: 768` in the upstream Gemini embedding payload, returning vectors scaled to the requested dimension
