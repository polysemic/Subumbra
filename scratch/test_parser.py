import re

# Mock whitelist from providers.json env_vars
WHITELIST = {
    "ANTHROPIC_KEY", "OPENAI_KEY", "GROQ_KEY", "DEEPSEEK_KEY", "CEREBRAS_API_KEY",
    "GEMINI_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY", "TOGETHER_AI_API_KEY",
    "XAI_API_KEY", "GITHUB_KEY", "SLACK_KEY", "SENDGRID_KEY",
    # Variations mentioned in Proposal 4
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY", "GITHUB_TOKEN", "SLACK_BOT_TOKEN"
}

# Mock exclusions
EXCLUSIONS = {
    "LITELLM_MASTER_KEY", "WEBUI_SECRET_KEY", "N8N_ENCRYPTION_KEY", "DATABASE_URL"
}

def parse_env(path):
    detected = {}
    skipped = []
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            key, val = line.split('=', 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            
            if key in WHITELIST:
                detected[key] = val
            elif key in EXCLUSIONS or "DATABASE" in key or "POSTGRES" in key:
                skipped.append(key)
    return detected, skipped

if __name__ == "__main__":
    detected, skipped = parse_env("/home/eric/git/Subumbra/scratch/test_migration.env")
    print(f"Detected: {list(detected.keys())}")
    print(f"Skipped: {skipped}")
    
    # Truth Check
    assert "ANTHROPIC_API_KEY" in detected
    assert "OPENAI_API_KEY" in detected
    assert "GROQ_API_KEY" in detected
    assert "LITELLM_MASTER_KEY" not in detected
    assert "DATABASE_URL" not in detected
    print("\n✓ Parser logic verified.")
