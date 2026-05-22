import maxminddb
import ipaddress

def filter_ips_by_coordinates(db_path, min_lat, max_lat, min_lon, max_lon):
    matching_networks = []

    with maxminddb.open_database(db_path) as reader:
        #network is IPv4 or IPv6 addr
        for network, record in reader:
            
            #get latlong from record
            location = record.get('location', {})
            latitude = location.get('latitude')
            longitude = location.get('longitude')
            
            if latitude is not None and longitude is not None:
                if min_lat <= latitude <= max_lat and min_lon <= longitude <= max_lon:
                    matching_networks.append({
                        "cidr": str(network),
                        "latitude": latitude,
                        "longitude": longitude,
                        "country": record.get('country', {}).get('names', {}).get('en', 'Unknown'),
                        "city": record.get('city', {}).get('names', {}).get('en', 'Unknown')
                    })
                    
    return matching_networks

if __name__ == "__main__":
    DB_FILE = "GeoLite2-City.mmdb"

    LAT, LON = 38.38474274, -93.93743896
    MIN_LAT, MIN_LON, MAX_LAT, MAX_LON = LAT-0.3, LON-0.3, LAT+0.3, LON+0.3
    
    results = filter_ips_by_coordinates(DB_FILE, MIN_LAT, MAX_LAT, MIN_LON, MAX_LON)
    
    print(f"\nFound {len(results)} IP networks in this range:\n")
    for item in results:
        print(f"CIDR: {item['cidr']:<18} | Location: {item['city']}, {item['country']} ({item['latitude']}, {item['longitude']})")