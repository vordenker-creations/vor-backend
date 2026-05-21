import httpx
import asyncio

async def test_bridge():
    # Use your MacBook's Tailscale IP here
    mac_ip = "http://100.80.253.23:11434"
    
    async with httpx.AsyncClient() as client:
        try:
            # We are asking the MacBook for its list of models
            response = await client.post(
                f"{mac_ip}/api/generate", 
                json={                     
                    # 2. Your payload goes into the 'json' parameter
                    "model": "gemma4-local:latest",
                    "prompt": "Explain the concept of an asynchronous event loop in one sentence.",
                    "stream": False # Important: add this so you get the full response at once
                },
                timeout=30 # Bump the timeout to 30s—AI inference takes time!
            )
            data = response.json()
            answer = data.get("response", "No answer field in response")
            print(f"AI Response: {answer}")
        #     print(f"Connection success! MacBook says: {data}")
        except Exception as e:
            print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_bridge())