import urllib.request

def test_connection(url):
    print(f"Attempting to connect to {url}...")
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            print(f"Successfully connected to {url}.")
            print(f"HTTP Status Code: {response.getcode()}")
    except Exception as e:
        print(f"FAILED to connect to {url}.")
        print(f"Error: {e}")

if __name__ == "__main__":
    test_connection("https://www.google.com")
    print("-"*20)
    test_connection("https://api.telegram.org")
