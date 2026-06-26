import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

# Set provider to 'ollama' or 'claude_proxy'
LLM_PROVIDER = "claude_proxy"

class LLMClient:
    def __init__(self):
        self.provider = LLM_PROVIDER
        
        # Ollama config
        self.ollama_url = os.getenv("OLLAMA_API_URL")
        self.ollama_model = "gemma4:latest"
        
        # Claude Proxy config
        self.claude_url = os.getenv("CLAUDE_API_URL")
        self.claude_user = os.getenv("CLAUDE_USER")
        self.claude_pass = os.getenv("CLAUDE_PASSWORD")

    def query(self, prompt: str, system_prompt: str = "You are a helpful assistant.") -> str:
        """
        Routes the query to the configured provider.
        """
        if self.provider == "claude_proxy":
            return self._query_claude_proxy(prompt, system_prompt)
        else:
            return self._query_ollama(prompt, system_prompt)

    def _query_ollama(self, prompt: str, system_prompt: str) -> str:
        headers = {"Content-Type": "application/json"}
        payload = {
            "model": self.ollama_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0,
            "stream": False
        }
        try:
            response = requests.post(self.ollama_url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"Ollama error: {e}")
            return ""

    def _query_claude_proxy(self, prompt: str, system_prompt: str) -> str:
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache"
        }
        # Using basic auth as seen in curl -u
        auth = (self.claude_user, self.claude_pass)
        
        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt}
                    ]
                }
            ]
        }
        try:
            response = requests.post(self.claude_url, headers=headers, json=payload, auth=auth)
            response.raise_for_status()
            # Most invoke-style endpoints return content in a similar structure; 
            # adjusting based on typical Claude response format if it follows Anthropic's style
            data = response.json()
            if "content" in data and isinstance(data["content"], list):
                return data["content"][0].get("text", "")
            return str(data)
        except Exception as e:
            print(f"Claude proxy error: {e}")
            return ""
