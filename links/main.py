from src.llm_client import LLMClient
from src.extractor import DataExtractor
from src.data_processor import DataProcessor
import os

def main():
    # Initialize components
    client = LLMClient()
    extractor = DataExtractor(client)
    
    # Initialize data processor 
    data_dir = os.path.join(os.getcwd(), "data", "berlin-schachtensee")
    processor = DataProcessor(data_dir)
    
    # Preprocess CSV data
    context_data = processor.get_context_data()

    print("Starting analysis of dataset connections...")
    result = extractor.extract(context_data=context_data)
    
    print("Analysis Result:")
    print(result["raw_response"])

if __name__ == "__main__":
    main()
