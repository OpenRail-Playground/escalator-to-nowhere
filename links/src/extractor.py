from .llm_client import LLMClient

class DataExtractor:
    def __init__(self, client: LLMClient):
        self.client = client

    def extract(self, context_data: dict) -> dict:
        """
        Extracts connections between different datasets.
        """
        # 1. Define prompts
        system_prompt = (
            "You are a data integration specialist. Your task is to analyze the provided JSON datasets "
            "and identify logical connections, relationships, or matches between them (e.g., shared IDs, "
            "matching names, or related descriptions). Return the findings in a clear, structured format."
        )
        
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
