"""角色相关 API"""

from fastapi import APIRouter

from app.agents.character_templates import load_all_templates
from app.models.session import CharacterTemplate

router = APIRouter(prefix="/characters", tags=["characters"])


@router.get("/", response_model=list[CharacterTemplate])
async def list_characters():
    """获取所有可用的 AI 角色模板。"""
    templates = load_all_templates()
    return list(templates.values())


@router.get("/{character_id}", response_model=CharacterTemplate)
async def get_character(character_id: str):
    """根据 ID 获取角色模板。"""
    from app.agents.character_templates import get_template

    try:
        return get_template(character_id)
    except ValueError as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=str(e))