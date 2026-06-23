import pandas as pd
import json
import os

# Configurable constants for columns
TPS_TABLE_NAME = "sap-tps-gesamt"
TPS_COLUMNS = ["id", "name", "oeffentliche_flaeche", "bahnhof"]
BAHNSTEIG_TABLE_NAME = "sap-bahnsteig-eqs-gesamt"
BAHNSTEIG_COLUMNS = ["id", "technischer_platz"]
GLEIS_TABLE_NAME = "sap-gleise-gesamt"
GLEIS_COLUMNS = ["equipment", "name"]
AUFZUG_TABLE_NAME = "sap-aufzug-eqs-gesamt"
AUFZUG_COLUMNS = ["id", "technischer_platz", "name", "ausftextlichebeschreibung", "bahnhof"]

class DataProcessor:
    def __init__(self, data_dir: str, bahnhof_ids: list = None):
        self.data_dir = data_dir
        self.bahnhof_ids = bahnhof_ids
        self.tps_data, self.equipment_data = self.get_tps_equipment_data()

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
            # only use entries of specified bahnhof_ids if provided
            if self.bahnhof_ids is not None and "bahnhof" in filtered_df.columns:
                filtered_df = filtered_df[filtered_df["bahnhof"].isin(self.bahnhof_ids)]
            return filtered_df
        except Exception as e:
            raise RuntimeError(f"Error processing {filename}: {e}")

    def gleis_enhanced_tps_data(self) -> pd.DataFrame:
        """Processes the gleis data and returns a DataFrame with specific columns."""
        tps_data = self.load_and_filter_csv(TPS_TABLE_NAME + ".csv", TPS_COLUMNS)
        gleis_data = self.load_and_filter_csv(GLEIS_TABLE_NAME + ".csv", GLEIS_COLUMNS)
        bahnsteig_data = self.load_and_filter_csv(BAHNSTEIG_TABLE_NAME + ".csv", BAHNSTEIG_COLUMNS)

        bahnsteig_data.rename(columns={"id": "id_bahnsteig"}, inplace=True)
        gleis_data.rename(columns={"id": "id_gleis"}, inplace=True)

        gleis_grouped = gleis_data.groupby("equipment")["name"].apply(lambda x: ", ".join(x.dropna())).reset_index()
        gleis_grouped.rename(columns={"name": "gleis_names"}, inplace=True)

        tps_data_with_bahnsteig = pd.merge(tps_data, bahnsteig_data, left_on="id", right_on="technischer_platz", how="left")
        final_df = pd.merge(tps_data_with_bahnsteig, gleis_grouped, left_on="id_bahnsteig", right_on="equipment", how="left")
        return final_df[[*tps_data.columns, "gleis_names"]]


    def get_tps_equipment_data(self) -> tuple:
        tps_data = self.gleis_enhanced_tps_data()
        tps_data = tps_data[tps_data['oeffentliche_flaeche'] == 'X'].drop(columns=['oeffentliche_flaeche'])
        aufzug_data = self.load_and_filter_csv(AUFZUG_TABLE_NAME + ".csv", AUFZUG_COLUMNS)
        aufzug_data = aufzug_data[aufzug_data['technischer_platz'].isin(tps_data['id'])]

        temp_dir = os.path.join(os.path.dirname(self.data_dir), "temp")
        os.makedirs(temp_dir, exist_ok=True)
        tps_data.sort_values(by="bahnhof").to_csv(os.path.join(temp_dir, "tps_data.csv"), index=False)
        aufzug_data.sort_values(by="bahnhof").to_csv(os.path.join(temp_dir, "aufzug_data.csv"), index=False)

        return tps_data, aufzug_data


    def get_context_data(self, bahnhof_id: int) -> dict:
        """Processes specific files and returns a dictionary of data."""
        tps_data = self.tps_data[self.tps_data['bahnhof'] == bahnhof_id].drop(columns=['bahnhof'])
        equipment_data = self.equipment_data[self.equipment_data['bahnhof'] == bahnhof_id].drop(columns=['bahnhof'])

        tps_json = tps_data.to_json(orient="records")
        aufzug_json = equipment_data.to_json(orient="records")

        return {
            "tps": tps_json,
            "equipment": aufzug_json
        }