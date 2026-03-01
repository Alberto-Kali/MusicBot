from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import BOT_USERNAME
from lib.controldb import (
    add_tracks_to_library,
    add_track_to_playlist,
    clone_playlist_to_user,
    create_playlist,
    delete_playlist,
    get_or_create_user,
    get_playlist,
    get_playlist_by_token,
    get_playlist_tracks,
    get_track_by_id,
    get_user_library,
    get_user_playlists,
    remove_track_from_playlist,
)
from states import PlaylistStates
from utils.keyboards import main_menu_keyboard

router = Router()


def playlists_list_keyboard(playlists):
    builder = InlineKeyboardBuilder()
    for playlist in playlists[:30]:
        builder.button(text=f"🎵 {playlist.name}", callback_data=f"playlist:open:{playlist.id}")
    builder.button(text="➕ Создать плейлист", callback_data="playlist:create")
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


async def show_playlists_menu(callback: CallbackQuery):
    user = await get_or_create_user(callback.from_user.id)
    playlists = await get_user_playlists(user.id)

    if not playlists:
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ Создать плейлист", callback_data="playlist:create")
        builder.button(text="🏠 Главное меню", callback_data="menu:main")
        builder.adjust(1)
        await callback.message.edit_text("У вас пока нет плейлистов.", reply_markup=builder.as_markup())
        await callback.answer()
        return

    await callback.message.edit_text("Ваши плейлисты:", reply_markup=playlists_list_keyboard(playlists))
    await callback.answer()


async def get_owned_playlist(callback: CallbackQuery, playlist_id: int):
    user = await get_or_create_user(callback.from_user.id)
    playlist = await get_playlist(playlist_id)
    if not playlist or playlist.user_id != user.id:
        await callback.answer("Нет доступа к плейлисту", show_alert=True)
        return None
    return playlist


@router.callback_query(F.data == "menu:playlists")
async def menu_playlists(callback: CallbackQuery):
    await show_playlists_menu(callback)


@router.callback_query(F.data == "playlist:create")
async def playlist_create_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PlaylistStates.waiting_for_name)
    await callback.message.edit_text(
        "Отправьте название нового плейлиста одним сообщением.",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.message(PlaylistStates.waiting_for_name)
async def playlist_create_finish(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Название слишком короткое. Минимум 2 символа.")
        return

    user = await get_or_create_user(message.from_user.id)
    playlist = await create_playlist(user.id, name[:80])
    await state.clear()

    builder = InlineKeyboardBuilder()
    builder.button(text="Открыть плейлист", callback_data=f"playlist:open:{playlist.id}")
    builder.button(text="К списку плейлистов", callback_data="menu:playlists")
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(1)

    await message.answer(f"Плейлист «{playlist.name}» создан.", reply_markup=builder.as_markup())


async def render_playlist(callback: CallbackQuery, playlist_id: int):
    playlist = await get_owned_playlist(callback, playlist_id)
    if not playlist:
        return

    tracks = await get_playlist_tracks(playlist.id)

    builder = InlineKeyboardBuilder()
    for track in tracks[:40]:
        builder.button(text=f"{track.artist} - {track.title}", callback_data=f"playlist:track:{playlist.id}:{track.id}")
    builder.button(text="➕ Добавить из библиотеки", callback_data=f"playlist:add_from_lib:{playlist.id}")
    builder.button(text="🔗 Поделиться", callback_data=f"playlist:share:{playlist.id}")
    builder.button(text="🗑 Удалить плейлист", callback_data=f"playlist:delete:{playlist.id}")
    builder.button(text="🔙 К плейлистам", callback_data="menu:playlists")
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(1)

    header = f"Плейлист: {playlist.name}\nТреков: {len(tracks)}"
    await callback.message.edit_text(header, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("playlist:open:"))
async def playlist_open(callback: CallbackQuery):
    playlist_id = int(callback.data.split(":")[-1])
    await render_playlist(callback, playlist_id)


@router.callback_query(F.data.startswith("playlist:add_from_lib:"))
async def playlist_add_from_library(callback: CallbackQuery):
    playlist_id = int(callback.data.split(":")[-1])
    playlist = await get_owned_playlist(callback, playlist_id)
    if not playlist:
        return

    user = await get_or_create_user(callback.from_user.id)
    tracks = await get_user_library(user.id)

    if not tracks:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 К плейлисту", callback_data=f"playlist:open:{playlist_id}")
        builder.button(text="🏠 Главное меню", callback_data="menu:main")
        builder.adjust(1)
        await callback.message.edit_text("Библиотека пуста, добавлять нечего.", reply_markup=builder.as_markup())
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    for track in tracks[:40]:
        builder.button(
            text=f"{track.artist} - {track.title}",
            callback_data=f"playlist:add_track:{playlist_id}:{track.id}",
        )
    builder.button(text="🔙 К плейлисту", callback_data=f"playlist:open:{playlist_id}")
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(1)

    await callback.message.edit_text("Выберите трек из библиотеки:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("playlist:add_track:"))
async def playlist_add_track(callback: CallbackQuery):
    _, _, playlist_id, track_id = callback.data.split(":")
    playlist = await get_owned_playlist(callback, int(playlist_id))
    if not playlist:
        return

    added = await add_track_to_playlist(int(playlist_id), int(track_id))
    await callback.answer("Трек добавлен" if added else "Трек уже есть в плейлисте")
    await render_playlist(callback, int(playlist_id))


@router.callback_query(F.data.startswith("playlist:track:"))
async def playlist_track_actions(callback: CallbackQuery):
    _, _, playlist_id, track_id = callback.data.split(":")
    playlist = await get_owned_playlist(callback, int(playlist_id))
    if not playlist:
        return

    track = await get_track_by_id(int(track_id))
    if not track:
        await callback.answer("Трек не найден", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="⬇ Скачать", callback_data=f"lib:download:{track.video_id}")
    builder.button(text="❌ Убрать из плейлиста", callback_data=f"playlist:remove_track:{playlist_id}:{track.id}")
    builder.button(text="🔙 К плейлисту", callback_data=f"playlist:open:{playlist_id}")
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(1)

    await callback.message.edit_text(
        f"{track.artist} — {track.title}",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("playlist:remove_track:"))
async def playlist_remove_track(callback: CallbackQuery):
    _, _, playlist_id, track_id = callback.data.split(":")
    playlist = await get_owned_playlist(callback, int(playlist_id))
    if not playlist:
        return

    await remove_track_from_playlist(int(playlist_id), int(track_id))
    await callback.answer("Трек удалён из плейлиста")
    await render_playlist(callback, int(playlist_id))


@router.callback_query(F.data.startswith("playlist:delete:"))
async def playlist_delete(callback: CallbackQuery):
    playlist_id = int(callback.data.split(":")[-1])
    user = await get_or_create_user(callback.from_user.id)
    deleted = await delete_playlist(user.id, playlist_id)
    if deleted:
        await callback.answer("Плейлист удалён")
    else:
        await callback.answer("Не удалось удалить", show_alert=True)
    await show_playlists_menu(callback)


@router.callback_query(F.data.startswith("playlist:share:"))
async def playlist_share(callback: CallbackQuery):
    playlist_id = int(callback.data.split(":")[-1])
    playlist = await get_owned_playlist(callback, playlist_id)
    if not playlist:
        return

    if BOT_USERNAME:
        link = f"https://t.me/{BOT_USERNAME}?start=pl_{playlist.share_token}"
        text = f"Ссылка для шаринга плейлиста «{playlist.name}»:\n{link}"
    else:
        text = (
            "Для шаринга задайте BOT_USERNAME в окружении.\n"
            f"Токен плейлиста: pl_{playlist.share_token}"
        )

    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 К плейлисту", callback_data=f"playlist:open:{playlist.id}")
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(1)

    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


async def show_shared_playlist(message: Message, token: str, current_user_id: int):
    playlist = await get_playlist_by_token(token)
    if not playlist:
        await message.answer("Плейлист по ссылке не найден.", reply_markup=main_menu_keyboard())
        return

    tracks = await get_playlist_tracks(playlist.id)
    builder = InlineKeyboardBuilder()
    for track in tracks[:40]:
        builder.button(text=f"{track.artist} - {track.title}", callback_data=f"lib:download:{track.video_id}")

    if playlist.user_id == current_user_id:
        builder.button(text="Открыть мой плейлист", callback_data=f"playlist:open:{playlist.id}")
    else:
        builder.button(text="➕ Добавить плейлист к себе", callback_data=f"share:add_playlist:{token}")
        builder.button(text="📚 Добавить все треки в библиотеку", callback_data=f"share:add_lib:{token}")
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(1)

    await message.answer(
        f"Открыт shared-плейлист: {playlist.name}\nТреков: {len(tracks)}",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("share:add_playlist:"))
async def add_shared_playlist_to_user(callback: CallbackQuery):
    token = callback.data.split(":", 2)[2]
    playlist = await get_playlist_by_token(token)
    if not playlist:
        await callback.answer("Плейлист не найден", show_alert=True)
        return

    user = await get_or_create_user(callback.from_user.id)
    if playlist.user_id == user.id:
        await callback.answer("Это уже ваш плейлист", show_alert=True)
        return

    copied = await clone_playlist_to_user(playlist.id, user.id)
    if not copied:
        await callback.answer("Не удалось добавить плейлист", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="Открыть плейлист", callback_data=f"playlist:open:{copied.id}")
    builder.button(text="🧩 Мои плейлисты", callback_data="menu:playlists")
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(1)
    await callback.message.edit_text(
        f"Плейлист «{copied.name}» добавлен в ваши плейлисты.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer("Готово")


@router.callback_query(F.data.startswith("share:add_lib:"))
async def add_shared_playlist_tracks_to_library(callback: CallbackQuery):
    token = callback.data.split(":", 2)[2]
    playlist = await get_playlist_by_token(token)
    if not playlist:
        await callback.answer("Плейлист не найден", show_alert=True)
        return

    tracks = await get_playlist_tracks(playlist.id)
    user = await get_or_create_user(callback.from_user.id)
    added, total = await add_tracks_to_library(user.id, [t.id for t in tracks])

    builder = InlineKeyboardBuilder()
    builder.button(text="📚 Моя библиотека", callback_data="menu:library")
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(1)
    await callback.message.edit_text(
        f"Добавлено в библиотеку: {added} из {total} треков.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer("Готово")
