from aiogram.fsm.state import State, StatesGroup

class SearchStates(StatesGroup):
    waiting_for_query = State()

class LibraryStates(StatesGroup):
    browsing = State()