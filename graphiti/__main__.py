"""MCP server for Personal Assistant / Graphiti."""
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .config import load_config
from .cli import create_episode_store, close_episode_store
from .mcp.logger import McpEpisodeLogger, McpTurn


async def main():
    """Run the MCP server."""
    server = Server("personal-assistant")
    
    # Load config and create episode store
    config = load_config()
    episode_store = create_episode_store(config)
    
    # Create MCP logger to save conversations
    mcp_logger = McpEpisodeLogger(episode_store=episode_store, config=config)
    conversation_id = str(uuid.uuid4())  # Generate a conversation ID for this session
    
    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List available tools."""
        return [
            Tool(
                name="search_episodes",
                description="Search across all episodes (Gmail, Drive, Calendar, Slack, MCP) for text matching the query",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query to find in episode text content"
                        },
                        "source": {
                            "type": "string",
                            "description": "Optional filter by source: gmail, gdrive, calendar, slack, mcp",
                            "enum": ["gmail", "gdrive", "calendar", "slack", "mcp"]
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results (default: 10)",
                            "default": 10
                        }
                    },
                    "required": ["query"]
                }
            ),
            Tool(
                name="get_episode_stats",
                description="Get statistics about ingested episodes by source",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),
            Tool(
                name="list_recent_episodes",
                description="List the most recent episodes, optionally filtered by source",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "Optional filter by source: gmail, gdrive, calendar, slack, mcp",
                            "enum": ["gmail", "gdrive", "calendar", "slack", "mcp"]
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results (default: 20)",
                            "default": 20
                        }
                    }
                }
            )
        ]
    
    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        """Handle tool calls."""
        
        # Log the user's tool call (request)
        user_turn = McpTurn(
            message_id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role="user",
            content=f"Tool: {name}\nArguments: {json.dumps(arguments, indent=2)}",
            timestamp=datetime.now(timezone.utc),
            metadata={"tool_name": name, "arguments": arguments}
        )
        mcp_logger.log_turn(user_turn)
        
        def _search_episodes(query: str, source: str | None = None, limit: int = 10):
            driver = getattr(episode_store, "_driver", None)
            if not driver:
                return {"error": "Episode store not available"}
            
            with driver.session() as session:
                cypher = """
                MATCH (e:Episode {group_id: $group_id})
                WHERE e.text IS NOT NULL
                  AND toLower(e.text) CONTAINS toLower($query)
                """
                params = {"group_id": config.group_id, "query": query, "limit": limit}
                
                if source:
                    cypher += " AND e.source = $source"
                    params["source"] = source
                
                cypher += """
                RETURN e.episode_id AS episode_id,
                       e.source AS source,
                       e.native_id AS native_id,
                       e.valid_at AS valid_at,
                       e.text AS text,
                       e.metadata_json AS metadata_json
                ORDER BY e.valid_at DESC
                LIMIT $limit
                """
                
                result = session.run(cypher, params)
                episodes = []
                for record in result:
                    episodes.append({
                        "episode_id": record["episode_id"],
                        "source": record["source"],
                        "native_id": record["native_id"],
                        "valid_at": record["valid_at"],
                        "text": record["text"][:500] if record["text"] else None,
                        "metadata": json.loads(record["metadata_json"]) if record["metadata_json"] else {}
                    })
                return episodes
        
        def _get_stats():
            driver = getattr(episode_store, "_driver", None)
            if not driver:
                return {"error": "Episode store not available"}
            
            with driver.session() as session:
                result = session.run("""
                    MATCH (e:Episode {group_id: $group_id})
                    RETURN e.source AS source, count(*) AS count
                    ORDER BY source
                """, {"group_id": config.group_id})
                
                stats = {}
                for record in result:
                    stats[record["source"]] = record["count"]
                return {"stats": stats, "total": sum(stats.values())}
        
        def _list_recent(source: str | None = None, limit: int = 20):
            driver = getattr(episode_store, "_driver", None)
            if not driver:
                return {"error": "Episode store not available"}
            
            with driver.session() as session:
                cypher = """
                MATCH (e:Episode {group_id: $group_id})
                """
                params = {"group_id": config.group_id, "limit": limit}
                
                if source:
                    cypher += " WHERE e.source = $source"
                    params["source"] = source
                
                cypher += """
                RETURN e.episode_id AS episode_id,
                       e.source AS source,
                       e.native_id AS native_id,
                       e.valid_at AS valid_at,
                       e.text AS text,
                       e.metadata_json AS metadata_json
                ORDER BY e.valid_at DESC
                LIMIT $limit
                """
                
                result = session.run(cypher, params)
                episodes = []
                for record in result:
                    episodes.append({
                        "episode_id": record["episode_id"],
                        "source": record["source"],
                        "native_id": record["native_id"],
                        "valid_at": record["valid_at"],
                        "text": record["text"][:500] if record["text"] else None,
                        "metadata": json.loads(record["metadata_json"]) if record["metadata_json"] else {}
                    })
                return episodes
        
        try:
            if name == "search_episodes":
                result = await asyncio.to_thread(
                    _search_episodes,
                    arguments.get("query"),
                    arguments.get("source"),
                    arguments.get("limit", 10)
                )
            elif name == "get_episode_stats":
                result = await asyncio.to_thread(_get_stats)
            elif name == "list_recent_episodes":
                result = await asyncio.to_thread(
                    _list_recent,
                    arguments.get("source"),
                    arguments.get("limit", 20)
                )
            else:
                result = {"error": f"Unknown tool: {name}"}
            
            # Log the assistant's response
            response_text = json.dumps(result, indent=2, default=str)
            assistant_turn = McpTurn(
                message_id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                role="assistant",
                content=response_text,
                timestamp=datetime.now(timezone.utc),
                metadata={"tool_name": name, "result_type": type(result).__name__}
            )
            mcp_logger.log_turn(assistant_turn)
            
            # Flush to database asynchronously
            await asyncio.to_thread(mcp_logger.flush)
            
            return [TextContent(type="text", text=response_text)]
        except Exception as e:
            error_text = f"Error: {str(e)}"
            # Log the error
            error_turn = McpTurn(
                message_id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                role="assistant",
                content=error_text,
                timestamp=datetime.now(timezone.utc),
                metadata={"tool_name": name, "error": str(e)}
            )
            mcp_logger.log_turn(error_turn)
            await asyncio.to_thread(mcp_logger.flush)
            
            return [TextContent(type="text", text=error_text)]
    
    # Run the server
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())

