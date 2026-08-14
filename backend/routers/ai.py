"""Routes: ai (extracted from backend/main.py)."""
from __future__ import annotations


from fastapi import APIRouter, Depends, Request

import backend.main as bm

router = APIRouter()


@bm.app.post('/api/ai/chat')
async def ai_chat(request: Request, _: None=Depends(bm._require_admin_token)) -> dict:
    """AI Trading Agent chat endpoint (admin only — тратит внешнюю LLM-квоту)."""
    try:
        body = await request.json()
    except Exception:
        return {'ok': False, 'error': 'Invalid JSON'}
    message = body.get('message', '').strip()
    if not message:
        return {'ok': False, 'error': 'Empty message'}
    autonomy = body.get('autonomy', 'confirm')
    try:
        from mexc_monitor.ai import TradingAgent, AgentConfig, AutonomyLevel, OpenAIProvider, MARKET_TOOLS
        cfg = bm._load_ai_config()
        api_key = bm.os.environ.get(cfg.get('api_key_env', 'OPENAI_API_KEY'), '')
        if not api_key:
            return {'ok': False, 'error': f"API key not set. Set {cfg.get('api_key_env', 'OPENAI_API_KEY')} environment variable."}
        provider = OpenAIProvider(api_key=api_key, model=cfg.get('model', 'gpt-4o-mini'))
        agent_config = AgentConfig(system_prompt=cfg.get('system_prompt', ''), autonomy=AutonomyLevel(autonomy), temperature=cfg.get('temperature', 0.3), max_tokens=cfg.get('max_tokens', 4096))
        agent = TradingAgent(provider=provider, config=agent_config)
        for tool_def in MARKET_TOOLS:
            agent.register_tool(name=tool_def['name'], description=tool_def['description'], parameters=tool_def['parameters'], handler=tool_def['handler'])
        turn = await agent.chat(message)
        return {'ok': True, 'response': turn.assistant_response, 'tool_calls': turn.tool_calls, 'tool_results': turn.tool_results}
    except Exception as e:
        bm.logger.exception('AI chat error')
        return {'ok': False, 'error': str(e)}


@bm.app.get('/api/ai/config')
def ai_config() -> dict:
    """Get AI agent configuration."""
    cfg = bm._load_ai_config()
    env_key = cfg.get('api_key_env', 'OPENAI_API_KEY')
    has_key = bool(bm.os.environ.get(env_key, '').strip())
    return {'ok': True, 'provider': cfg.get('provider', 'openai'), 'model': cfg.get('model', 'gpt-4o-mini'), 'autonomy': cfg.get('autonomy', 'confirm'), 'has_api_key': has_key, 'telegram_enabled': cfg.get('telegram_enabled', False)}


@bm.app.get('/api/ai/telegram/status')
def telegram_status() -> dict:
    """Get Telegram bot status."""
    cfg = bm._load_ai_config()
    token_env = cfg.get('telegram_bot_token_env', 'TELEGRAM_BOT_TOKEN')
    chat_env = cfg.get('telegram_chat_id_env', 'TELEGRAM_CHAT_ID')
    has_token = bool(bm.os.environ.get(token_env, '').strip())
    has_chat = bool(bm.os.environ.get(chat_env, '').strip())
    enabled = cfg.get('telegram_enabled', False)
    return {'ok': True, 'enabled': enabled, 'has_token': has_token, 'has_chat_id': has_chat, 'token_env': token_env, 'chat_env': chat_env, 'running': enabled and has_token}
