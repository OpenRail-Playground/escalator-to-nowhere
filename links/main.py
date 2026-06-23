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
    csv_result = extractor.extract(context_data=context_data)
    
    # Save the result to a CSV file
    output_path = os.path.join(os.getcwd(), "out", "connections.csv")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(csv_result)
    
    print(f"Analysis complete. Results saved to {output_path}")

if __name__ == "__main__":
    main()
