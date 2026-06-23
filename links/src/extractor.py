from importlib.resources import path

from .llm_client import LLMClient
from pathlib import Path

SYSTEM_PROMPT_PATH = "prompts/system-prompt.txt"

class DataExtractor:
    def __init__(self, client: LLMClient):
        self.client = client
    

    def extract(self, context_data: dict) -> dict:
        """
        Extracts connections between different datasets.
        """
        # 1. Define prompts
        system_prompt = Path(SYSTEM_PROMPT_PATH).read_text(encoding="utf-8").strip()
        
        # Build context string
        context_str = "Analyze the following datasets for connections:\n"
        if "tps" in context_data:
            context_str += f"\nTPS Data:\n{context_data['tps']}\n"
        if "aufzug" in context_data:
            context_str += f"\nAufzug Data:\n{context_data['aufzug']}\n"

        # 2. Query LLM
        response = self.client.query(context_str, system_prompt=system_prompt)
        
        # 3. Process answer (to be implemented later)
        # For now, just returning the raw response in a dict
        extracted_data = {
            "raw_response": response,
            "parsed_data": {} # Placeholder
        }
        
        return extracted_data
