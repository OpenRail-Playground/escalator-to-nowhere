import pandas as pd
import json
import os

# Configurable constants for columns
TPS_COLUMNS = ["id", "standardized_name"]
AUFZUG_COLUMNS = ["id", "standardized_name", "ausftextlichebeschreibung"]

class DataProcessor:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir

    def load_and_filter_csv(self, filename: str, columns: list) -> str:
        """Loads a CSV, filters columns, and returns a JSON string."""
        path = os.path.join(self.data_dir, filename)
        if not os.path.exists(path):
            print(f"File not found: {path}")
            return "[]"
        
        try:
            df = pd.read_csv(path)
            # Filter for existing columns to avoid errors
            existing_cols = [col for col in columns if col in df.columns]
            filtered_df = df[existing_cols]
            return filtered_df.to_json(orient="records")
        except Exception as e:
            print(f"Error processing {filename}: {e}")
            return "[]"

    def get_context_data(self) -> dict:
        """Processes specific files and returns a dictionary of data."""
        tps_data = self.load_and_filter_csv("sap-tps-berlin-schlachtensee.csv", TPS_COLUMNS)
        aufzug_data = self.load_and_filter_csv("sap-aufzug-eqs-berlin-schlachtensee.csv", AUFZUG_COLUMNS)

        return {
            "tps": tps_data,
            "aufzug": aufzug_data
        }
