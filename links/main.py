from src.llm_client import LLMClient
from src.extractor import DataExtractor
from src.data_processor import DataProcessor
import os

BAHNHOF_IDS = [
    557
]

def main():
    # Initialize components
    client = LLMClient()
    extractor = DataExtractor(client)
    
    # Initialize data processor 
    data_dir = os.path.join(os.getcwd(), "data", "gesamt")
    processor = DataProcessor(data_dir, bahnhof_ids=BAHNHOF_IDS)
    
    output_path = os.path.join(os.getcwd(), "out", "connections.csv")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Empty the connections.csv file before starting the loop
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("")

    for bahnhof_id in BAHNHOF_IDS:
        print(f"Processing data for Bahnhof ID: {bahnhof_id}")
        context_data = processor.get_context_data(bahnhof_id)
        csv_result = extractor.extract(context_data=context_data)
        with open(output_path, "a", encoding="utf-8") as f:
            if not csv_result.endswith("\n"):
                csv_result += "\n"
            f.write(csv_result)
    
    print(f"Analysis complete. Results saved to {output_path}")

if __name__ == "__main__":
    main()
