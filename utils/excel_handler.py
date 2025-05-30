import os
import logging
import random
from typing import Optional, Dict, Any, Set, Union

from openpyxl import Workbook, load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from settings import (
    EXCEL_FILENAME,
    ALL_EXPECTED_HEADERS,
    BATTERY_SHEET_NAMES, BASE_HEADERS,  # New import for battery sheets
)

logger = logging.getLogger(__name__)


def initialize_excel_file():
    """
    Initializes the Excel file.
    - Creates it with a default 'Sheet1' if it doesn't exist.
    - Ensures specified BATTERY_SHEET_NAMES (e.g., "контроль_Ц", "лекарство_Ц") exist.
    - For each of these sheets (default and battery specific), if it's new or empty,
      it populates the first row with ALL_EXPECTED_HEADERS.
    - If a sheet exists and has headers, it attempts to add any missing headers
      from ALL_EXPECTED_HEADERS to the end of its first row.
    """
    wb = None
    created_new_file = False
    if not os.path.exists(EXCEL_FILENAME):
        wb = Workbook()
        # openpyxl creates a default sheet named 'Sheet'. We can rename it or use it.
        # For simplicity, let's ensure our named sheets exist.
        # The default 'Sheet' might remain if not in BATTERY_SHEET_NAMES.
        # Or, remove default sheet if BATTERY_SHEET_NAMES is primary focus:
        # if "Sheet" in wb.sheetnames and BATTERY_SHEET_NAMES:
        #     del wb["Sheet"] # if you want only named sheets from battery
        created_new_file = True
        logger.info(f"Файл '{EXCEL_FILENAME}' не существует, будет создан.")
    else:
        try:
            wb = load_workbook(EXCEL_FILENAME)
        except InvalidFileException:
            logger.error(
                f"Файл '{EXCEL_FILENAME}' поврежден или не является валидным Excel файлом. "
                "Создание нового файла."
            )
            wb = Workbook()  # Create a new workbook instance
            created_new_file = True  # Treat as if new file was created
        except Exception as e_load:
            logger.error(f"Не удалось загрузить '{EXCEL_FILENAME}': {e_load}. Создание нового файла.")
            wb = Workbook()
            created_new_file = True

    if wb is None:  # Should not happen if logic above is correct
        logger.error(f"Не удалось инициализировать Workbook для '{EXCEL_FILENAME}'.")
        return

    # Ensure all battery-specific sheets exist
    sheets_to_ensure = list(BATTERY_SHEET_NAMES)
    # Optionally add a default sheet name if you want one managed this way too
    # default_sheet_name = "ОбщиеРезультаты"
    # if default_sheet_name not in sheets_to_ensure:
    # sheets_to_ensure.append(default_sheet_name)

    # If new file and it has the default "Sheet", and we have battery sheets, remove default.
    if created_new_file and "Sheet" in wb.sheetnames and BATTERY_SHEET_NAMES:
        if len(wb.sheetnames) > 1 or wb.active.title == "Sheet":  # Check if it's the only one or active
            try:
                del wb["Sheet"]
            except KeyError:
                pass  # Already gone or never existed with that exact name

    made_header_changes_in_any_sheet = False

    for sheet_name in sheets_to_ensure:
        ws = None
        if sheet_name not in wb.sheetnames:
            ws = wb.create_sheet(title=sheet_name)
            logger.info(f"Создан новый лист '{sheet_name}' в '{EXCEL_FILENAME}'.")
            # For a brand new sheet, always add all headers
            ws.append(ALL_EXPECTED_HEADERS)
            logger.info(f"Все заголовки добавлены в новый лист '{sheet_name}'.")
            made_header_changes_in_any_sheet = True
        else:
            ws = wb[sheet_name]
            # Sheet exists, check if it's empty or needs header updates
            if ws.max_row == 0 or (ws.max_row == 1 and all(c.value is None for c in ws[1])):
                # Sheet is effectively empty, add all headers
                if ws.max_row > 0:  # Clear the potentially empty first row
                    ws.delete_rows(1, ws.max_row)
                ws.append(ALL_EXPECTED_HEADERS)
                logger.info(f"Лист '{sheet_name}' был пуст. Все заголовки добавлены.")
                made_header_changes_in_any_sheet = True
            else:
                # Sheet exists and has content, check/add missing headers
                current_header_values = [cell.value for cell in ws[1]]
                existing_header_set = set(filter(None, current_header_values))

                # Find the first completely empty column in the header row to append new headers
                # This assumes headers are contiguous from column A.
                first_empty_header_col = len(current_header_values) + 1
                for i, cell_val in enumerate(current_header_values):
                    if cell_val is None:
                        first_empty_header_col = i + 1
                        break

                newly_added_sheet_headers = []
                for header_to_add in ALL_EXPECTED_HEADERS:
                    if header_to_add not in existing_header_set:
                        ws.cell(row=1, column=first_empty_header_col).value = header_to_add
                        newly_added_sheet_headers.append(header_to_add)
                        existing_header_set.add(header_to_add)  # Track for this run
                        first_empty_header_col += 1

                if newly_added_sheet_headers:
                    logger.info(f"В лист '{sheet_name}' добавлены недостающие заголовки: {newly_added_sheet_headers}")
                    made_header_changes_in_any_sheet = True

    if created_new_file and not BATTERY_SHEET_NAMES and not wb.sheetnames:
        # If no battery sheets specified and file was new & empty, create a default sheet
        ws_default = wb.create_sheet(title="Sheet1")  # Or your preferred default name
        ws_default.append(ALL_EXPECTED_HEADERS)
        logger.info(f"Создан стандартный лист 'Sheet1' со всеми заголовками в новом файле.")
        made_header_changes_in_any_sheet = True
    elif not wb.sheetnames:  # If after all logic, workbook has no sheets (e.g. default deleted, no battery names)
        ws_fallback = wb.create_sheet(title="Sheet1")
        ws_fallback.append(ALL_EXPECTED_HEADERS)
        logger.info(f"Файл не содержал листов. Создан стандартный лист 'Sheet1' со всеми заголовками.")
        made_header_changes_in_any_sheet = True

    if made_header_changes_in_any_sheet or created_new_file:
        try:
            wb.save(EXCEL_FILENAME)
            logger.info(f"Файл '{EXCEL_FILENAME}' успешно сохранен после инициализации/обновления листов и заголовков.")
        except Exception as e_save_final:
            logger.error(f"Не удалось сохранить '{EXCEL_FILENAME}' после инициализации: {e_save_final}")


# --- New Profile Management Functions (remain largely the same as provided by user) ---
def generate_unique_id(existing_ids: Set[str]) -> Optional[int]:
    min_uid, max_uid = 1000000, 9999999
    attempts = 0
    max_attempts = 1000
    while attempts < max_attempts:
        candidate_uid = random.randint(min_uid, max_uid)
        if str(candidate_uid) not in existing_ids:
            return candidate_uid
        attempts += 1
    logger.critical(
        f"Не удалось сгенерировать уникальный UID после {max_attempts} попыток."
    )
    return None


def create_user_profile_in_excel(
        name: str, age: int, tgid: int, target_sheet_name: Optional[str] = None
) -> Optional[int]:
    # Modified to accept target_sheet_name, defaults to active if not specified
    try:
        if not os.path.exists(EXCEL_FILENAME):
            logger.info(f"Excel файл '{EXCEL_FILENAME}' не найден. Инициализация...")
            initialize_excel_file()
            if not os.path.exists(EXCEL_FILENAME):
                logger.error("Excel файл не создан после инициализации.")
                return None

        wb = load_workbook(EXCEL_FILENAME)
        ws = None
        if target_sheet_name and target_sheet_name in wb.sheetnames:
            ws = wb[target_sheet_name]
        elif wb.sheetnames:  # Get the first sheet if target not specified or not found
            ws = wb[wb.sheetnames[0]]
            logger.warning(
                f"Target sheet '{target_sheet_name}' not found or not specified for profile creation. Using sheet '{ws.title}'.")
        else:  # No sheets exist at all
            logger.error("В Excel файле нет листов для создания профиля.")
            return None

        excel_headers = [cell.value for cell in ws[1]] if ws.max_row > 0 else []
        if not excel_headers or "Unique ID" not in excel_headers:
            logger.error(
                f"В листе '{ws.title}' отсутствует заголовок 'Unique ID' или лист пуст. Профиль не будет создан здесь.")
            return None  # Cannot create profile without Unique ID header

        uid_col_idx_excel = excel_headers.index("Unique ID")
        existing_ids_from_excel: Set[str] = set()
        for row_tuple in ws.iter_rows(min_row=2, values_only=True):
            if (
                    uid_col_idx_excel < len(row_tuple)
                    and row_tuple[uid_col_idx_excel] is not None
            ):
                existing_ids_from_excel.add(str(row_tuple[uid_col_idx_excel]))

        new_uid = generate_unique_id(existing_ids_from_excel)
        if new_uid is None:
            return None

        new_row_data = [""] * len(excel_headers)  # Match length of actual headers in sheet

        # Map data to actual column indices in the sheet
        header_to_index_map_sheet = {header: i for i, header in enumerate(excel_headers)}

        if "Telegram ID" in header_to_index_map_sheet:
            new_row_data[header_to_index_map_sheet["Telegram ID"]] = tgid
        if "Unique ID" in header_to_index_map_sheet:
            new_row_data[header_to_index_map_sheet["Unique ID"]] = new_uid
        if "Name" in header_to_index_map_sheet:
            new_row_data[header_to_index_map_sheet["Name"]] = name
        if "Age" in header_to_index_map_sheet:
            new_row_data[header_to_index_map_sheet["Age"]] = age

        # Fill any other BASE_HEADERS if they exist in this sheet
        for bh_header in BASE_HEADERS:  # BASE_HEADERS from settings
            if bh_header in header_to_index_map_sheet and not new_row_data[header_to_index_map_sheet[bh_header]]:
                if bh_header == "Telegram ID":
                    new_row_data[header_to_index_map_sheet[bh_header]] = tgid
                elif bh_header == "Unique ID":
                    new_row_data[header_to_index_map_sheet[bh_header]] = new_uid
                elif bh_header == "Name":
                    new_row_data[header_to_index_map_sheet[bh_header]] = name
                elif bh_header == "Age":
                    new_row_data[header_to_index_map_sheet[bh_header]] = age

        ws.append(new_row_data)
        wb.save(EXCEL_FILENAME)
        logger.info(
            f"Новый пользователь зарегистрирован в Excel (лист: '{ws.title}'): UID {new_uid}, TGID {tgid}, Имя '{name}', Возраст {age}"
        )
        return new_uid
    except Exception as e:
        logger.error(
            f"Ошибка регистрации пользователя в Excel (Имя '{name}', TGID {tgid}, Лист: '{target_sheet_name}'): {e}",
            exc_info=True,
        )
        return None


def find_user_profile_in_excel(
        uid_to_find: str, current_tgid: Optional[int] = None, target_sheet_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    # Modified to accept target_sheet_name
    try:
        if not os.path.exists(EXCEL_FILENAME):
            logger.warning(f"Excel файл '{EXCEL_FILENAME}' не найден при поиске UID {uid_to_find}.")
            return None

        wb = load_workbook(EXCEL_FILENAME)
        ws = None
        if target_sheet_name and target_sheet_name in wb.sheetnames:
            ws = wb[target_sheet_name]
        elif wb.sheetnames:  # Default to first sheet
            ws = wb[wb.sheetnames[0]]
            logger.warning(
                f"Target sheet '{target_sheet_name}' for profile search not found/specified. Using sheet '{ws.title}'.")
        else:
            logger.error("В Excel файле нет листов для поиска профиля.")
            return None

        if ws.max_row < 1:
            logger.warning(f"Лист '{ws.title}' пуст (нет заголовков) при поиске UID {uid_to_find}.")
            return None

        header_row_excel = [cell.value for cell in ws[1]]
        if "Unique ID" not in header_row_excel:
            logger.error(f"В листе '{ws.title}' отсутствует заголовок 'Unique ID'.")
            return None

        uid_col_idx = header_row_excel.index("Unique ID")
        tg_id_col_idx = header_row_excel.index("Telegram ID") if "Telegram ID" in header_row_excel else -1
        name_col_idx = header_row_excel.index("Name") if "Name" in header_row_excel else -1
        age_col_idx = header_row_excel.index("Age") if "Age" in header_row_excel else -1

        found_profile_data: Optional[Dict[str, Any]] = None
        row_to_update_tgid_excel_num = -1

        for row_idx_excel, row_values in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            excel_uid_value = row_values[uid_col_idx] if uid_col_idx < len(row_values) else None
            if excel_uid_value is not None and str(excel_uid_value) == uid_to_find:
                found_profile_data = {
                    "unique_id": str(excel_uid_value),
                    "telegram_id": str(row_values[tg_id_col_idx]) if tg_id_col_idx != -1 and tg_id_col_idx < len(
                        row_values) and row_values[tg_id_col_idx] is not None else str(current_tgid or "N/A"),
                    "name": str(row_values[name_col_idx]) if name_col_idx != -1 and name_col_idx < len(row_values) and
                                                             row_values[name_col_idx] is not None else "N/A",
                    "age": str(row_values[age_col_idx]) if age_col_idx != -1 and age_col_idx < len(row_values) and
                                                           row_values[age_col_idx] is not None else "N/A",
                }
                if current_tgid and tg_id_col_idx != -1:
                    current_tg_id_in_excel_str = str(row_values[tg_id_col_idx]) if tg_id_col_idx < len(row_values) and \
                                                                                   row_values[
                                                                                       tg_id_col_idx] is not None else None
                    if current_tg_id_in_excel_str != str(current_tgid):
                        row_to_update_tgid_excel_num = row_idx_excel
                break

        if row_to_update_tgid_excel_num != -1 and tg_id_col_idx != -1:
            ws.cell(row=row_to_update_tgid_excel_num, column=tg_id_col_idx + 1, value=str(current_tgid))
            wb.save(EXCEL_FILENAME)
            logger.info(f"Обновлен Telegram ID для UID {uid_to_find} на {current_tgid} в листе '{ws.title}'.")
            if found_profile_data:
                found_profile_data["telegram_id"] = str(current_tgid)

        return found_profile_data
    except Exception as e:
        logger.error(f"Ошибка поиска профиля UID {uid_to_find} в Excel (лист: '{target_sheet_name}'): {e}",
                     exc_info=True)
        return None


def get_all_user_data_from_excel(uid_to_find: str, target_sheet_name: Optional[str] = None) -> Dict[str, Any]:
    # Modified to accept target_sheet_name
    data_for_display: Dict[str, Any] = {}
    try:
        if not os.path.exists(EXCEL_FILENAME):
            return {"error": f"Файл данных '{EXCEL_FILENAME}' не найден."}

        wb = load_workbook(EXCEL_FILENAME)
        ws = None
        if target_sheet_name and target_sheet_name in wb.sheetnames:
            ws = wb[target_sheet_name]
        elif wb.sheetnames:  # Default to first sheet
            ws = wb[wb.sheetnames[0]]
            logger.warning(
                f"Target sheet '{target_sheet_name}' for data retrieval not found/specified. Using sheet '{ws.title}'.")
        else:
            return {"error": "В Excel файле нет листов для извлечения данных."}

        if ws.max_row < 1:
            return {"info": f"Лист Excel '{ws.title}' пуст (нет заголовков)."}

        header_row_values = [cell.value for cell in ws[1]]
        if "Unique ID" not in header_row_values:
            return {"error": f"Столбец 'Unique ID' не найден в заголовках листа '{ws.title}'."}

        uid_col_idx_excel = header_row_values.index("Unique ID")
        found_in_excel = False

        for row_tuple in ws.iter_rows(min_row=2, values_only=True):
            excel_uid_val = row_tuple[uid_col_idx_excel] if uid_col_idx_excel < len(row_tuple) else None
            if excel_uid_val is not None and str(excel_uid_val) == uid_to_find:
                found_in_excel = True
                for i, header_name in enumerate(header_row_values):
                    if header_name is None: continue
                    val_from_excel = row_tuple[i] if i < len(row_tuple) else None
                    display_val = val_from_excel if val_from_excel is not None else "нет данных"
                    if isinstance(val_from_excel, float) and val_from_excel.is_integer():
                        display_val = int(val_from_excel)
                    data_for_display[str(header_name)] = str(display_val)
                break

        if not found_in_excel:
            data_for_display["info"] = f"Профиль с UID {uid_to_find} не найден в листе '{ws.title}'."

    except InvalidFileException:
        data_for_display["error"] = f"Файл '{EXCEL_FILENAME}' поврежден. Невозможно загрузить данные."
    except ValueError as ve:
        data_for_display["error"] = f"Ошибка конфигурации заголовков в файле Excel: {ve}"
    except Exception as e:
        data_for_display[
            "error"] = f"Ошибка при загрузке данных из Excel для UID {uid_to_find} (лист: '{target_sheet_name}'): {e}"

    return data_for_display


# --- Existing Generic Result Check Functions (remain the same, they use wb.active implicitly) ---
# These might need modification if they are to check specific sheets for the battery context,
# but for now, the request focuses on saving to specific sheets.
async def check_if_results_exist_generic(
        profile_unique_id: Union[str, int], result_header_to_check: str, target_sheet_name: Optional[str] = None
) -> bool:
    if not profile_unique_id:
        logger.warning("Excel check: profile_unique_id не предоставлен.")
        return False
    try:
        uid_to_check_str = str(int(profile_unique_id))
    except ValueError:
        logger.error(f"Excel check: Неверный формат profile_unique_id: {profile_unique_id}.")
        return False

    try:
        if not os.path.exists(EXCEL_FILENAME):
            logger.info(f"Excel файл {EXCEL_FILENAME} не найден для проверки результатов (generic).")
            return False

        wb = load_workbook(EXCEL_FILENAME)
        ws = None
        if target_sheet_name and target_sheet_name in wb.sheetnames:
            ws = wb[target_sheet_name]
        elif wb.sheetnames:
            ws = wb[wb.sheetnames[0]]  # Default to first sheet if target not specified or found
        else:  # No sheets exist
            return False

        if ws.max_row < 2: return False  # No data beyond headers

        header_row_values = [cell.value for cell in ws[1]]
        try:
            uid_col_idx = header_row_values.index("Unique ID")
            result_col_idx = header_row_values.index(result_header_to_check)
        except ValueError:  # Header not found
            logger.warning(
                f"Excel check (generic): Заголовок 'Unique ID' или '{result_header_to_check}' не найден в листе '{ws.title}'.")
            return False

        for row_idx in range(2, ws.max_row + 1):
            uid_cell = ws.cell(row=row_idx, column=uid_col_idx + 1)
            if uid_cell.value is not None and str(uid_cell.value) == uid_to_check_str:
                result_cell = ws.cell(row=row_idx, column=result_col_idx + 1)
                if result_cell.value is not None:
                    return True
        return False
    except Exception as e:
        logger.error(
            f"Ошибка при проверке результатов (generic) для UID {profile_unique_id}, заголовок '{result_header_to_check}', лист '{target_sheet_name}': {e}",
            exc_info=True)
        return False


# Specific checks remain, but now can optionally take target_sheet_name if needed by battery logic
async def check_if_corsi_results_exist(profile_unique_id: Union[str, int],
                                       target_sheet_name: Optional[str] = None) -> bool:
    return await check_if_results_exist_generic(profile_unique_id, "Corsi - Max Correct Sequence Length",
                                                target_sheet_name)


async def check_if_stroop_results_exist(profile_unique_id: Union[str, int],
                                        target_sheet_name: Optional[str] = None) -> bool:
    return await check_if_results_exist_generic(profile_unique_id, "Stroop Part1 Time (s)", target_sheet_name)


async def check_if_reaction_time_results_exist(profile_unique_id: Union[str, int],
                                               target_sheet_name: Optional[str] = None) -> bool:
    return await check_if_results_exist_generic(profile_unique_id, "ReactionTime_Status", target_sheet_name)


async def check_if_verbal_fluency_results_exist(profile_unique_id: Union[str, int],
                                                target_sheet_name: Optional[str] = None) -> bool:
    return await check_if_results_exist_generic(profile_unique_id, "VerbalFluency_WordCount", target_sheet_name)


async def check_if_mental_rotation_results_exist(profile_unique_id: Union[str, int],
                                                 target_sheet_name: Optional[str] = None) -> bool:
    return await check_if_results_exist_generic(profile_unique_id, "MentalRotation_CorrectAnswers", target_sheet_name)


async def check_if_raven_matrices_results_exist(profile_unique_id: Union[str, int],
                                                target_sheet_name: Optional[str] = None) -> bool:
    return await check_if_results_exist_generic(profile_unique_id, "RavenMatrices_CorrectAnswers", target_sheet_name)


# New check for STM (optional, if needed for individual runs outside battery)
async def check_if_stm_results_exist(profile_unique_id: Union[str, int],
                                     target_sheet_name: Optional[str] = None) -> bool:
    # Assuming "STM_Recalled_Words" is a key indicator of a completed STM test
    return await check_if_results_exist_generic(profile_unique_id, "STM_Recalled_Words", target_sheet_name)
