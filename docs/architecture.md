# Subumbra — Architecture Overview

This guide explains the technical design of Subumbra. It bridges the gap between a high-level security concept and the actual services running on your hardware.

---

## 🏗️ Core Runtime Shape

This diagram shows how a request travels from your application to the final provider.

```text
App Integration (LiteLLM, Open WebUI,LibreChat, n8n, etc.)
      ↓ [uses Adapter Token]
subumbra-proxy
      ↓ [fetches encrypted data]
subumbra-keys
      ↓ [sends question + encrypted data]
Cloudflare Worker + Durable Object
      ↓ [decrypts key & makes call]
Provider API (OpenAI, Anthropic, etc.)
```

---

## 🛡️ For Everyone: The "Security Shield"

Subumbra acts as a **Security Shield** for your API keys. Instead of giving your sensitive master keys to every app you use, you give those apps a "proxy token."

- **Your apps never see your real keys.** They only talk to the `subumbra-proxy`.
- **Your keys are stored locally, but they are scrambled.** Even if someone steals your server, they can't read the keys in `subumbra-keys`.
- **Only the Cloudflare Worker can unscramble them.** The authority to use your key is split between your server and a secure cloud environment.

---

## 🧱 The Architecture Components

Subumbra is composed of three main services that work together to secure your credentials.

### 1. subumbra-proxy (The Entry Point)
This is the local service your apps talk to. It acts as a transparent middleman. When it receives a request:
- It verifies that the app's **Adapter Token** is valid.
- It identifies which provider key is being requested.
- It manages the communication between your local server and the secure cloud environment.

### 2. subumbra-keys (The Secure Storage)
This is where your encrypted API keys live. They are stored as **encrypted blobs**. 
- These blobs are useless on their own.
- They are physically separated from the "decryption authority" (the Cloudflare Worker).
- This service ensures that sensitive data stays on your hardware until the moment it is needed.

### 3. Cloudflare Worker (The Decryption Authority)
This is a secure, private environment in the cloud that holds the "decryption key." 
- When the `subumbra-proxy` sends it an encrypted blob, the Worker verifies the request.
- It unscrambles the key inside its private memory for a fraction of a second.
- It performs the actual request to the provider and returns the answer.
- **The real API key is never sent back to your server.**

---

## 🔐 Core Security Concepts

### Split-Trust Model
Subumbra uses a "Split-Trust" architecture. Security is maintained because no single component has everything needed to leak a key:
- **Your Server** has the encrypted data but no way to decrypt it.
- **Cloudflare** has the decryption authority but no access to your encrypted data unless you send it a specific request.

An attacker would need to compromise both your server and your Cloudflare account to gain access to your keys.

### Policy-Bound Encryption (AAD)
Every key is encrypted using its specific rules (like allowed paths or body size limits) as **Associated Authenticated Data (AAD)**. This creates a cryptographic "seal" between the key and its policy.
- If an attacker modifies the policy on your server to bypass restrictions, the Cloudflare Worker will fail to decrypt the key. 
- The "Rules" and the "Key" are mathematically bonded; you cannot have one without the other.

---

## 🗺️ Example System Flow

When you ask an AI a question through your App (configured to use Subumbra):

1.  **Request:** Your App sends a question to `subumbra-proxy` using a proxy token.
2.  **Fetch:** `subumbra-proxy` validates the token and fetches the **Encrypted Blob** from `subumbra-keys`.
3.  **Forward:** `subumbra-proxy` sends the question and the encrypted blob to your **Cloudflare Worker**.
4.  **Decrypt & Call:** The Worker verifies the "Policy Seal," unscrambles the key in memory, and calls the AI Provider (e.g., OpenAI).
5.  **Response:** The AI's answer is passed back to the app, and the Worker erases the key from memory.

---

## 📊 Summary: Why This Architecture?
Subumbra moves the "point of vulnerability" away from your individual apps and onto a single, hardened, split-trust pipeline. By decoupling the **storage** of a key from the **authority** to use it, you ensure that your most sensitive credentials are never "live" in any application configuration, protecting you from leaks, breaches, and accidental exposure.

---

### Related Documentation
- [README.md](../README.md) — Quick start and project overview.
- [Operator Guide](operator-guide.md) — Daily management and recovery.
- [Installation Guide](subumbra-install.md) — How to set up the stack.
- [Security Overview](security-overview.md) — Deeper dive into the cryptographic model.
