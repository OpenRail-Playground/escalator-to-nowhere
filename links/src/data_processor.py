import pandas as pd
import json
import os

# Configurable constants for columns
TPS_COLUMNS = ["id", "name", "oeffentliche_flaeche"]
BAHNSTEIG_COLUMNS = ["id", "technischer_platz"]
GLEIS_COLUMNS = ["equipment", "name"]
AUFZUG_COLUMNS = ["id", "technischer_platz", "name", "ausftextlichebeschreibung"]

class DataProcessor:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir

    def load_and_filter_csv(self, filename: str, columns: list) -> pd.DataFrame:
        """Loads a CSV, filters columns, and returns a df."""
        path = os.path.join(self.data_dir, filename)
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}") 
        
        try:
            df = pd.read_csv(path)
            # Filter for existing columns to avoid errors
            existing_cols = [col for col in columns if col in df.columns]
            filtered_df = df[existing_cols]
            return filtered_df
        except Exception as e:
            raise RuntimeError(f"Error processing {filename}: {e}")

    def gleis_enhanced_tps_data(self) -> pd.DataFrame:
        """Processes the gleis data and returns a DataFrame with specific columns."""
        tps_data = self.load_and_filter_csv("sap-tps-berlin-schlachtensee.csv", TPS_COLUMNS)
        gleis_data = self.load_and_filter_csv("sap-gleise-berlin-schlachtensee.csv", GLEIS_COLUMNS)
        bahnsteig_data = self.load_and_filter_csv("sap-bahnsteig-eqs-berlin-schlachtensee.csv", BAHNSTEIG_COLUMNS)

        bahnsteig_data.rename(columns={"id": "id_bahnsteig"}, inplace=True)
        gleis_data.rename(columns={"id": "id_gleis"}, inplace=True)

        gleis_grouped = gleis_data.groupby("equipment")["name"].apply(lambda x: ", ".join(x.dropna())).reset_index()
        gleis_grouped.rename(columns={"name": "gleis_names"}, inplace=True)

        tps_data_with_bahnsteig = pd.merge(tps_data, bahnsteig_data, left_on="id", right_on="technischer_platz", how="left")
        final_df = pd.merge(tps_data_with_bahnsteig, gleis_grouped, left_on="id_bahnsteig", right_on="equipment", how="left")
        return final_df[[*tps_data.columns, "gleis_names"]]


    def write_json(self, data: str, filename: str) -> None:
        """Writes a JSON string to the temp folder."""
        temp_dir = os.path.join(os.path.dirname(self.data_dir), "temp")
        os.makedirs(temp_dir, exist_ok=True)
        with open(os.path.join(temp_dir, filename), "w", encoding="utf-8") as f:
            f.write(data)

    def get_context_data(self) -> dict:
        """Processes specific files and returns a dictionary of data."""
        tps_data = self.gleis_enhanced_tps_data()
        tps_data = tps_data[tps_data['oeffentliche_flaeche'] == 'X'].drop(columns=['oeffentliche_flaeche'])
        aufzug_data = self.load_and_filter_csv("sap-aufzug-eqs-berlin-schlachtensee.csv", AUFZUG_COLUMNS)

        tps_json = tps_data.to_json(orient="records")
        aufzug_json = aufzug_data.to_json(orient="records")

        self.write_json(tps_json, "tps_data.json")
        self.write_json(aufzug_json, "aufzug_data.json")

        return {
            "tps": tps_json,
            "equipment": aufzug_json
        }