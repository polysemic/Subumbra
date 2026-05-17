# Attribution & Disclaimers

Subumbra is an independent open-source software project. This page provides important legal disclaimers of non-affiliation, trademarks notices, and license attributions for third-party libraries and services used by this project.

---

## 1. Disclaimers of Affiliation

Subumbra is **not affiliated, associated, authorized, endorsed by, or in any way officially connected** with any of the following companies, entities, or their subsidiaries and affiliates:

* **Cloudflare, Inc.** (operators of Cloudflare Workers, KV, and Durable Objects)
* **OpenAI, L.L.C.**
* **Anthropic PBC**
* **Google LLC** (Gemini API)
* **DeepSeek Inc.**
* **Groq, Inc.**
* **Mistral AI**
* **xAI Corp.**
* **Together AI (Together OS)**
* **BerriAI** (developers of LiteLLM)
* **Mintplex Labs Inc.** (developers of AnythingLLM)
* **MaximHQ** (developers of Bifrost)
* **LibreChat** (developers of LibreChat)
* **n8n.io** (developers of n8n workflow automation engine)
* **OpenWebUI** (developers of OpenWebUI frontend interface)

The use of any product names, API paths, or corporate names in this repository is strictly for interoperability, routing configuration, and technical integration purposes.

---

## 2. Trademark Notices

All trademarks, service marks, logos, and brand names referenced in this repository or documentation are the **sole property of their respective owners**. 

* **Cloudflare** is a registered trademark of Cloudflare, Inc.
* **OpenAI**, **GPT-4**, and **GPT-4o-mini** are trademarks of OpenAI, L.L.C.
* **Claude** and **Anthropic** are trademarks of Anthropic PBC.
* **Gemini** is a trademark of Google LLC.
* **AnythingLLM** is a trademark of Mintplex Labs Inc.
* **Bifrost** is a trademark of MaximHQ.
* **LibreChat** is a trademark of LibreChat.
* **n8n** is a trademark of n8n.io.
* **OpenWebUI** is a trademark of OpenWebUI.
* All other corporate names, brand names, and logos are used purely in an indexical sense to reference the target web APIs.

---

## 3. Third-Party Software Licenses

Subumbra relies on several open-source libraries and runtimes to function. The licenses and copyright notices for these major dependencies are attributed below:

### Runtimes & Infrastructure

#### Cloudflare Workers Runtime & Wrangler
* **Purpose:** High-performance serverless script execution and deployment.
* **License:** [MIT License](https://github.com/cloudflare/workers-sdk/blob/main/LICENSE-MIT) / [Apache License 2.0](https://github.com/cloudflare/workers-sdk/blob/main/LICENSE-APACHE)

### Python Bootstrap & Key Management Dependencies

#### Cryptography
* **Purpose:** Secure key derivation, signatures, and cryptographic primitives.
* **License:** [Apache License 2.0](https://github.com/pyca/cryptography/blob/main/LICENSE) / [BSD 3-Clause License](https://github.com/pyca/cryptography/blob/main/LICENSE.BSD)

#### PyYAML
* **Purpose:** Parsing YAML configuration manifests.
* **License:** [MIT License](https://github.com/yaml/pyyaml/blob/master/LICENSE)

### Local Security Proxy Dependencies

#### FastAPI
* **Purpose:** Web routing engine for the transparent local security proxy.
* **License:** [MIT License](https://github.com/fastapi/fastapi/blob/master/LICENSE)

#### Uvicorn
* **Purpose:** Asynchronous server process implementation.
* **License:** [BSD 3-Clause License](https://github.com/encode/uvicorn/blob/master/LICENSE.md)

#### HTTPX
* **Purpose:** Asynchronous HTTP client for forwarding requests to Cloudflare Workers.
* **License:** [BSD 3-Clause License](https://github.com/encode/httpx/blob/master/LICENSE.md)

#### Pydantic
* **Purpose:** Strong data validation and settings management.
* **License:** [MIT License](https://github.com/pydantic/pydantic/blob/main/LICENSE)

#### Starlette
* **Purpose:** Light-weight ASGI framework and toolkit.
* **License:** [BSD 3-Clause License](https://github.com/encode/starlette/blob/master/LICENSE.md)

### Management Dashboard (UI) Dependencies

#### Flask
* **Purpose:** Web interface for dashboard operations.
* **License:** [BSD 3-Clause License](https://github.com/pallets/flask/blob/main/LICENSE.rst)

#### Gunicorn
* **Purpose:** WSGI HTTP Server for production deployment.
* **License:** [MIT License](https://github.com/benoitc/gunicorn/blob/master/LICENSE)

### External Integrations

#### LiteLLM
* **Purpose:** High-level proxy routing and unified client completions gateway.
* **License:** [MIT License](https://github.com/BerriAI/litellm/blob/main/LICENSE)

#### PostgreSQL
* **Purpose:** Relational database backing for LiteLLM.
* **License:** [PostgreSQL License](https://www.postgresql.org/about/licence/)

#### OpenWebUI
* **Purpose:** Highly extensible, user-friendly WebUI for local and external LLM interaction.
* **License:** [MIT License](https://github.com/open-webui/open-webui/blob/main/LICENSE)

#### AnythingLLM
* **Purpose:** Multi-user enterprise-grade local AI chatbot and vector database.
* **License:** [MIT License](https://github.com/Mintplex-Labs/anything-llm/blob/master/LICENSE)

#### LibreChat
* **Purpose:** Feature-rich, customizable chat interface supporting multiple LLM integrations.
* **License:** [MIT License](https://github.com/danny-avila/LibreChat/blob/main/LICENSE)

#### Bifrost
* **Purpose:** Transparent AI proxy aggregator and request router.
* **License:** [MIT License](https://github.com/maximhq/bifrost/blob/main/LICENSE)

#### n8n
* **Purpose:** Node-based workflow automation engine for routing LLM nodes.
* **License:** [Faircode License (Sustainable Use)](https://github.com/n8n-io/n8n/blob/master/LICENSE.md)
