#!/usr/bin/env python3
"""
Clawith Agent Creator - Command-line tool to create agents

Usage:
    python create_agent.py --name "My Agent" --description "Agent description" --email "your@email.com" --password "your-password"
    python create_agent.py --name "My Agent" --description "Agent description" --api-key "your-api-key"
    python create_agent.py --help

Environment Variables:
    CLAWITH_API_KEY: Your Clawith API key (alternative to --api-key)
    CLAWITH_BASE_URL: Clawith API base URL (default: http://localhost:8008)
"""

import argparse
import json
import os
import sys

try:
    import requests
except ImportError:
    print("❌ Missing dependency: requests")
    print("   Install: pip install requests")
    sys.exit(1)


def create_agent(
    name: str,
    description: str,
    email: str = None,
    password: str = None,
    api_key: str = None,
    base_url: str = None,
    agent_type: str = "native",
    personality: str = None,
    boundaries: str = None,
    tenant_id: str = None,
) -> dict:
    """Create a new Clawith agent."""
    
    base_url = base_url or os.getenv("CLAWITH_BASE_URL", "http://localhost:8000")
    
    # Step 1: Get API key (either from login or env)
    if not api_key:
        if not email or not password:
            print("❌ Missing credentials. Provide either:")
            print("   --api-key YOUR_API_KEY")
            print("   OR --email and --password for login")
            sys.exit(1)
        
        # Login to get API key
        print(f"📝 Logging in as {email}...")
        login_resp = requests.post(
            f"{base_url}/api/auth/login",
            json={"email": email, "password": password},
            timeout=30,
        )
        
        if login_resp.status_code != 200:
            print(f"❌ Login failed: {login_resp.status_code}")
            print(f"   {login_resp.text[:200]}")
            sys.exit(1)
        
        login_data = login_resp.json()
        api_key = login_data.get("access_token")
        if not api_key:
            print("❌ Login succeeded but no access_token returned")
            sys.exit(1)
        print("✅ Login successful")
    
    # Step 2: Create agent
    print(f"🤖 Creating agent: {name}...")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "name": name,
        "role_description": description,
        "agent_type": agent_type,
    }
    
    if personality:
        payload["personality"] = personality
    
    if boundaries:
        payload["boundaries"] = boundaries
    
    if tenant_id:
        payload["tenant_id"] = tenant_id
    
    create_resp = requests.post(
        f"{base_url}/api/agents/",
        headers=headers,
        json=payload,
        timeout=60,
    )
    
    if create_resp.status_code != 201:
        print(f"❌ Agent creation failed: {create_resp.status_code}")
        print(f"   {create_resp.text[:500]}")
        sys.exit(1)
    
    agent_data = create_resp.json()
    print("✅ Agent created successfully!")
    
    return agent_data


def main():
    parser = argparse.ArgumentParser(
        description="Clawith Agent Creator - Create agents via command line",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create agent with API key from environment
  export CLAWITH_API_KEY=your-api-key
  python create_agent.py --name "Morty" --description "A helpful assistant"

  # Create agent with login credentials
  python create_agent.py --name "Morty" --description "A helpful assistant" \\
      --email "user@example.com" --password "secret"

  # Create agent with custom personality
  python create_agent.py --name "JARVIS" --description "AI assistant" \\
      --personality "You are a sophisticated AI assistant." \\
      --api-key "your-api-key"

  # Create OpenClaw type agent
  python create_agent.py --name "External Agent" --description "..." \\
      --agent-type "openclaw" --api-key "your-api-key"
        """,
    )
    
    parser.add_argument("--name", required=True, help="Agent name")
    parser.add_argument("--description", required=True, help="Agent role description")
    parser.add_argument("--email", help="Login email (alternative to --api-key)")
    parser.add_argument("--password", help="Login password (alternative to --api-key)")
    parser.add_argument("--api-key", help="Clawith API key (or set CLAWITH_API_KEY env)")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Clawith API URL")
    parser.add_argument("--agent-type", choices=["native", "openclaw"], default="native", help="Agent type")
    parser.add_argument("--personality", help="Agent personality/prompt")
    parser.add_argument("--boundaries", help="Agent boundaries/constraints")
    parser.add_argument("--tenant-id", help="Target tenant ID (admin only)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output JSON only")
    
    args = parser.parse_args()
    
    try:
        agent = create_agent(
            name=args.name,
            description=args.description,
            email=args.email,
            password=args.password,
            api_key=args.api_key or os.getenv("CLAWITH_API_KEY"),
            base_url=args.base_url,
            agent_type=args.agent_type,
            personality=args.personality,
            boundaries=args.boundaries,
            tenant_id=args.tenant_id,
        )
        
        if args.json_output:
            print(json.dumps(agent, indent=2))
        else:
            print("\n📋 Agent Details:")
            print(f"   ID: {agent.get('id')}")
            print(f"   Name: {agent.get('name')}")
            print(f"   Type: {agent.get('agent_type')}")
            print(f"   Status: {agent.get('status')}")
            if agent.get('api_key'):
                print(f"   API Key: {agent.get('api_key')}")
            print(f"\n🔗 Access: {args.base_url.replace('8008', '3008')}/agents/{agent.get('id')}")
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
