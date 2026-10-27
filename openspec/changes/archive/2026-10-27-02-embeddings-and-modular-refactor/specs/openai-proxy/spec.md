# Spec Delta

## ADDED Requirements

### Requirement: Embeddings Proxying and Schema Translation
The proxy SHALL expose an OpenAI-compatible `POST /v1/embeddings` endpoint that validates authentication, accepts standard OpenAI embedding payloads, translates requests into Google Cloud Vertex AI native prediction format (`:predict`), and returns embeddings in standard OpenAI format.

#### Scenario: Single string input embedding generation
- **WHEN** an authorized client sends a POST request to `/v1/embeddings` with `"input": "text to embed"` and a supported model name
- **THEN** the proxy transforms the input into an instance object `{"content": "text to embed"}`, calls the Vertex AI native prediction endpoint, and returns an OpenAI-compatible embedding response with `data[0].embedding` and usage statistics

#### Scenario: Multiple strings batch input embedding generation
- **WHEN** an authorized client sends a POST request to `/v1/embeddings` with `"input": ["text 1", "text 2"]`
- **THEN** the proxy transforms each item into a separate prediction instance, queries the Vertex AI prediction endpoint, and returns each embedding preserving its original index

#### Scenario: Optional dimensions parameter handling
- **WHEN** an authorized client provides an optional `"dimensions": 512` parameter in the request payload
- **THEN** the proxy passes `"parameters": {"outputDimensionality": 512}` to the Vertex AI prediction endpoint

#### Scenario: Model identifier normalization for embeddings
- **WHEN** an authorized client requests an embedding with an unprefixed model identifier (such as `text-embedding-004` or `gemini-embedding-001`)
- **THEN** the proxy correctly maps the model name to the Vertex AI publisher model resource path for prediction

#### Scenario: Empty or invalid input handling
- **WHEN** an authorized client sends an empty string or empty array in `"input"`
- **THEN** the proxy rejects the request immediately with an HTTP 400 Bad Request and an OpenAI-compatible error payload
