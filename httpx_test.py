import httpx
import asyncio

async def test_httpx_connection(url):
    print(f"Testing connection to {url} with httpx...")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            print(f"Successfully connected to {url} with httpx.")
            print(f"HTTP Status Code: {response.status_code}")
    except httpx.ConnectError as e:
        print(f"FAILED to connect to {url} with httpx.")
        print(f"Error: {e}")
        print("\nThis confirms the issue is with the httpx library.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    asyncio.run(test_httpx_connection("https://api.telegram.org/bot8430101507:AAGkn3NHv9YzjbcadR_hOHTrHK1ldq338sA/getMe"))
