from aiogram.fsm.state import State, StatesGroup


class SearchStates(StatesGroup):
    waiting_for_query = State()


class PlaylistStates(StatesGroup):
    waiting_for_name = State()


class RadioStates(StatesGroup):
    waiting_for_query = State()
