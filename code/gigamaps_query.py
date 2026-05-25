import os
import csv
import sys
import requests
from dotenv import load_dotenv

def get_schools_csv():
    #need this to make requests to gigamaps
    load_dotenv()
    GIGAMAPS_TOKEN = os.getenv("GIGAMAPS_TOKEN")
    
    #token is missing from dotenv
    if not GIGAMAPS_TOKEN:
        print("bad token. check the .env", file=sys.stderr)
        exit(1)

    #make request for all schools in US
    headers = { "Authorization": f"Bearer {GIGAMAPS_TOKEN}" }
    url = f"https://uni-ooi-giga-maps-service.azurewebsites.net/api/v1/schools_location/country/USA?"
    request = requests.get(url, headers=headers)

    #api call failed
    if request.status_code != 200:
        print("Gigamaps not available", file=sys.stderr)
        exit(1)

    response = request.json()
    schools = None if 'data' not in response else response["data"] 

    #no school data returned from api 
    if not schools:
        print("bad response", file=sys.stderr)

    #filter to just the schools in new york
    MIN_LAT = 40.5
    MAX_LAT = 45.05
    MIN_LONG = -79.8
    MAX_LONG = -71.8

    isNySchool = lambda lat, long: (MIN_LAT <= lat and lat <= MAX_LAT) and (MIN_LONG <= long and long <= MAX_LONG)
    
    ny_schools = []
    for school in schools:
        #skip schools in lat/long data isn't available
        if 'latitude' not in school or 'longitude' not in school:
            continue
        lat, long = school["latitude"], school["longitude"]

        #save all schools if they are in the bounding box
        if isNySchool(lat, long):
            ny_schools.append(school)

    #should be at least one school
    if len(ny_schools) == 0:
            print("no schools", file=sys.stderr)
            exit(1)

    print(ny_schools[0].keys())

    #not necessary but sorting by lat/long
    ny_schools.sort(key=lambda s: (s['latitude'], s['longitude']))

    #save schools to csv
    filename = "gigamaps_schools_ny.csv"
    fieldnames = ['school_name', 'longitude', 'latitude', 
                  'education_level', 'country_iso3_code', 
                  'giga_id_school', 'school_data_source']

    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(ny_schools)
    
    print(f"Saved {len(ny_schools)} schools to {filename}")



def main():
    get_schools_csv()
if __name__ == "__main__":
    main()

