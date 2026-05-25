from school_selection import import_schools, export_schools

def trim_urls(schools):
    for school in schools:
        if 'url' not in school:
            continue
        trimmed_url = trim_url(school['url'])
        school['trimmed_url'] = trimmed_url
    return schools

def trim_url(url):
    if not url:
        return url

    url = url.strip().lower()

    #remove https:// and http://
    if url.startswith("https://"):
        url = url[len("https://"):]
    elif url.startswith("http://"):
        url = url[len("http://"):]
    
    #remove www if the url has it
    if url.startswith("www."):
        url = url[len("www."):]

    #remove everything after first slash
    url = url.split("/")[0]

    return url.rstrip("/")


def main():
    schools = import_schools("../sampled_schools.csv")

    #sort by lat/long
    schools = sorted(schools, key=lambda s: (s['latitude'], s['longitude']))

    #trim urls
    schools = trim_urls(schools)

    export_schools(schools, "sampled_schools.csv")

if __name__ == "__main__":
    main()

