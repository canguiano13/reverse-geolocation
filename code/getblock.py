import requests

def get_census_block(lat, lon):
    url = f"https://geo.fcc.gov/api/census/block/find?latitude={lat}&longitude={lon}&format=json"
    try:
        response = requests.get(url, timeout=5).json()
        # Extracts the 15-digit block FIPS code
        print(f"Fetched block for {lat}, {lon}: {response['Block']['FIPS']}")
        return response['Block']['FIPS']
    except Exception as e:
        print(f"Error fetching block for {lat}, {lon}: {e}")
        return None
      
get_census_block(38.54525, -121.76452)