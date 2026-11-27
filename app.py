import calendar
import os
import sqlite3
import threading
import webbrowser
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from tkinter import messagebox

import customtkinter as ctk

from Module.address_lookup import DadataAPI
from Module.order_processing import (
    ADDRESS_PREFIX,
    ORDER_COLUMN_HEADERS,
    OrderTableRow,
    format_delivery_time,
    prepare_orders,
)
from Module.order_storage import OrderStore
from Module.telegram_bot import TelegramBot
from Module.zone_map import ZoneMapServer

# ==============================
# ЦВЕТА ПРИЛОЖЕНИЯ
# ==============================

BACKGROUND_COLOR = "#F5F7F4"
SIDEBAR_COLOR = "#244D3B"
SURFACE_COLOR = "#FFFFFF"
PRIMARY_COLOR = "#3D654D"
PRIMARY_HOVER_COLOR = "#355A43"
TEXT_COLOR = "#24352B"
MUTED_COLOR = "#78857C"
BORDER_COLOR = "#E6EBE5"
SIDEBAR_MUTED_COLOR = "#AFC7B6"
SIDEBAR_TEXT_COLOR = "#E1E9E3"
STATUS_COLOR = "#D7B978"
SUCCESS_COLOR = "#83D19A"
ERROR_COLOR = "#F28B82"
ORDER_COLUMN_WEIGHTS = (0.9, 1.3, 1.1, 2.3, 1.0, 0.9, 0.9, 1.4, 1.0)
MONTH_NAMES = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)
WEEKDAY_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


class App(ctk.CTk):

    def __init__(self):
        super().__init__()

        # Настройки окна
        ctk.set_appearance_mode("light")

        self.title("ТРАВА — доставка")
        self.geometry("1500x700")
        self.minsize(980, 620)
        self.configure(fg_color=BACKGROUND_COLOR)

        self.order_store = None
        self.database_error = None
        try:
            self.order_store = OrderStore()
        except (OSError, sqlite3.Error) as error:
            self.database_error = error

        # Данные заказов и курьеров хранятся локально в SQLite.
        self.orders = []
        self.couriers = []
        self.selected_date = date.today()
        self._calendar_popup = None
        self._calendar_month = self.selected_date.replace(day=1)
        self._side_panel = None
        self._side_panel_width = 0
        self.bind("<Configure>", self._resize_side_panel, add="+")
        self.telegram_stop_event = threading.Event()
        self.telegram_bot = None
        self.telegram_thread = None
        self.telegram_username = None
        self.zone_map_server = None
        self.bind_all("<Button-1>", self._close_calendar_on_outside_click, add="+")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Словарь для хранения страниц
        self.pages = {}

        # Создаём боковое меню
        self._create_sidebar()

        # Создаём страницы приложения
        self._create_pages()

        # Открываем страницу заказов
        self.show_page("Заказы")

        # Загружаем заказы при запуске
        self.load_orders()
        self._render_couriers_table()
        self.after(100, self._start_telegram_bot)

    # ==============================
    # БОКОВОЕ МЕНЮ
    # ==============================

    def _create_sidebar(self):

        sidebar = ctk.CTkFrame(
            self,
            width=230,
            corner_radius=0,
            fg_color=SIDEBAR_COLOR,
        )

        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # Название приложения
        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=22, pady=(28, 34))

        ctk.CTkLabel(
            brand,
            text="ТРАВА",
            font=ctk.CTkFont(family="Arial", size=27, weight="bold"),
            text_color=SURFACE_COLOR,
        ).pack(anchor="w")

        # Название раздела
        ctk.CTkLabel(
            sidebar,
            text="РАБОЧЕЕ ПРОСТРАНСТВО",
            font=ctk.CTkFont(family="Arial", size=10, weight="bold"),
            text_color=SIDEBAR_MUTED_COLOR,
        ).pack(anchor="w", padx=22, pady=(0, 10))

        # Кнопка заказов
        ctk.CTkButton(
            sidebar,
            text="Заказы",
            anchor="w",
            height=44,
            corner_radius=10,
            fg_color=PRIMARY_COLOR,
            hover_color=PRIMARY_HOVER_COLOR,
            text_color=SURFACE_COLOR,
            font=ctk.CTkFont(family="Arial", size=14),
            command=lambda: self.show_page("Заказы"),
        ).pack(fill="x", padx=14, pady=4)

        # Кнопка курьеров
        ctk.CTkButton(
            sidebar,
            text="Курьеры",
            anchor="w",
            height=44,
            corner_radius=10,
            fg_color=PRIMARY_COLOR,
            hover_color=PRIMARY_HOVER_COLOR,
            text_color=SURFACE_COLOR,
            font=ctk.CTkFont(family="Arial", size=14),
            command=lambda: self.show_page("Курьеры"),
        ).pack(fill="x", padx=14, pady=4)

        # Кнопка зон доставок
        ctk.CTkButton(
            sidebar,
            text="Зоны доставок",
            anchor="w",
            height=44,
            corner_radius=10,
            fg_color=PRIMARY_COLOR,
            hover_color=PRIMARY_HOVER_COLOR,
            text_color=SURFACE_COLOR,
            font=ctk.CTkFont(family="Arial", size=14),
            command=lambda: self.show_page("Зоны доставок"),
        ).pack(fill="x", padx=14, pady=4)

        # Блок состояния системы
        status_card = ctk.CTkFrame(
            sidebar,
            fg_color="transparent",
            corner_radius=0,
        )

        status_card.pack(side="bottom", fill="x", padx=22, pady=20)

        ctk.CTkFrame(
            status_card,
            height=1,
            fg_color="#456956",
        ).pack(fill="x", pady=(0, 16))

        ctk.CTkLabel(
            status_card,
            text="СОСТОЯНИЕ СИСТЕМЫ",
            font=ctk.CTkFont(family="Arial", size=10, weight="bold"),
            text_color=SIDEBAR_MUTED_COLOR,
        ).pack(anchor="w", pady=(0, 8))

        database_status_row = ctk.CTkFrame(status_card, fg_color="transparent")
        database_status_row.pack(fill="x", pady=4)
        self.database_status_dot = ctk.CTkLabel(
            database_status_row,
            text="●",
            font=ctk.CTkFont(family="Arial", size=9),
            text_color=STATUS_COLOR,
        )
        self.database_status_dot.pack(side="left", padx=(0, 7))
        self.database_status_label = ctk.CTkLabel(
            database_status_row,
            text="Проверка базы данных...",
            font=ctk.CTkFont(family="Arial", size=11),
            text_color=SIDEBAR_TEXT_COLOR,
            anchor="w",
            wraplength=178,
            justify="left",
        )
        self.database_status_label.pack(side="left", fill="x", expand=True)
        self._set_database_status(self.database_error)

        telegram_status_row = ctk.CTkFrame(status_card, fg_color="transparent")
        telegram_status_row.pack(fill="x", pady=4)
        self.telegram_status_dot = ctk.CTkLabel(
            telegram_status_row,
            text="●",
            font=ctk.CTkFont(family="Arial", size=9),
            text_color=STATUS_COLOR,
        )
        self.telegram_status_dot.pack(side="left", padx=(0, 7))
        self.telegram_status_label = ctk.CTkLabel(
            telegram_status_row,
            text="Проверка Telegram-бота...",
            font=ctk.CTkFont(family="Arial", size=11),
            text_color=SIDEBAR_TEXT_COLOR,
            anchor="w",
            wraplength=178,
            justify="left",
        )
        self.telegram_status_label.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            status_card,
            text="Заказы сохраняются локально",
            font=ctk.CTkFont(family="Arial", size=11),
            text_color=SIDEBAR_TEXT_COLOR,
            anchor="w",
            wraplength=178,
            justify="left",
        ).pack(anchor="w", pady=4, padx=(16, 0))

    def _set_database_status(self, error=None):
        if error is None:
            self.database_status_dot.configure(text_color=SUCCESS_COLOR)
            self.database_status_label.configure(
                text="База данных подключена",
                text_color=SUCCESS_COLOR,
            )
            return

        self.database_status_dot.configure(text_color=ERROR_COLOR)
        self.database_status_label.configure(
            text="Нет подключения к базе данных",
            text_color=ERROR_COLOR,
        )


    # ==============================
    # СОЗДАНИЕ СТРАНИЦ
    # ==============================

    def _create_pages(self):

        # Общий контейнер для всех страниц
        self.content_frame = ctk.CTkFrame(
            self, fg_color=BACKGROUND_COLOR, corner_radius=0
        )

        self.content_frame.pack(side="left", fill="both", expand=True, padx=42, pady=36)

        # Сохраняем страницы в словаре
        self.pages["Заказы"] = self._create_orders_page()
        self.pages["Курьеры"] = self._create_couriers_page()
        self.pages["Зоны доставок"] = self._create_zones_page()

    # ==============================
    # СТРАНИЦА ЗАКАЗОВ
    # ==============================

    def _create_orders_page(self):

        page = ctk.CTkFrame(
            self.content_frame, fg_color=BACKGROUND_COLOR, corner_radius=0
        )

        page.place(relwidth=1, relheight=1)

        # Заголовок
        ctk.CTkLabel(
            page,
            text="Заказы",
            font=ctk.CTkFont(family="Arial", size=30, weight="bold"),
            text_color=TEXT_COLOR,
        ).pack(anchor="w")

        # Выбор даты
        date_controls = ctk.CTkFrame(page, fg_color="transparent")
        date_controls.pack(anchor="w", pady=(16, 0))

        ctk.CTkLabel(
            date_controls,
            text="Дата доставки",
            font=ctk.CTkFont(family="Arial", size=12),
            text_color=MUTED_COLOR,
        ).pack(anchor="w", pady=(0, 6))

        date_actions = ctk.CTkFrame(date_controls, fg_color="transparent")
        date_actions.pack(anchor="w")

        self.date_picker_button = ctk.CTkButton(
            date_actions,
            text=self.selected_date.strftime("%d.%m.%Y") + "   ▾",
            width=170,
            height=38,
            corner_radius=10,
            fg_color=SURFACE_COLOR,
            hover_color="#EEF3EE",
            border_width=1,
            border_color=BORDER_COLOR,
            text_color=TEXT_COLOR,
            font=ctk.CTkFont(family="Arial", size=13),
            anchor="w",
            command=self._toggle_calendar,
        )
        self.date_picker_button.pack(side="left")

        self.create_order_button = ctk.CTkButton(
            date_actions,
            text="Создать заказ",
            width=140,
            height=38,
            corner_radius=10,
            fg_color=PRIMARY_COLOR,
            hover_color=PRIMARY_HOVER_COLOR,
            text_color=SURFACE_COLOR,
            font=ctk.CTkFont(family="Arial", size=13),
            command=self._open_order_form,
        )
        self.create_order_button.pack(side="left", padx=(8, 0))

        self.refresh_orders_button = ctk.CTkButton(
            date_actions,
            text="Обновить",
            width=120,
            height=38,
            corner_radius=10,
            fg_color=PRIMARY_COLOR,
            hover_color=PRIMARY_HOVER_COLOR,
            text_color=SURFACE_COLOR,
            font=ctk.CTkFont(family="Arial", size=13),
            command=self.load_orders,
        )
        self.refresh_orders_button.pack(side="left", padx=(8, 0))

        # Основной блок таблицы заказов
        table = ctk.CTkFrame(
            page,
            fg_color=SURFACE_COLOR,
            corner_radius=14,
            border_width=1,
            border_color=BORDER_COLOR,
        )
        table.pack(fill="both", expand=True, pady=(30, 0))

        header = ctk.CTkFrame(
            table,
            fg_color="#EEF3EE",
            corner_radius=10,
        )
        header.pack(fill="x", padx=(10, 22), pady=(10, 6))
        for column, weight in enumerate(ORDER_COLUMN_WEIGHTS):
            header.grid_columnconfigure(
                column,
                weight=int(weight * 10),
                uniform="order_columns",
            )

        for column, title in enumerate(ORDER_COLUMN_HEADERS):
            header_label = ctk.CTkLabel(
                header,
                text=title,
                font=ctk.CTkFont(family="Arial", size=12, weight="bold"),
                text_color=TEXT_COLOR,
                anchor="w",
                justify="left",
                wraplength=140,
            )
            header_label.bind(
                "<Configure>",
                lambda _event, label=header_label: self._fit_table_label(label),
            )
            header_label.grid(row=0, column=column, sticky="ew", padx=10, pady=12)

        self.orders_table_body = ctk.CTkFrame(table, fg_color=SURFACE_COLOR)
        self.orders_table_body.pack(fill="both", expand=True, padx=(10, 6), pady=(0, 8))

        self.orders_scrollable_frame = ctk.CTkScrollableFrame(
            self.orders_table_body,
            fg_color=SURFACE_COLOR,
            corner_radius=10,
            scrollbar_button_color=PRIMARY_COLOR,
            scrollbar_button_hover_color=PRIMARY_HOVER_COLOR,
        )
        self.orders_scrollable_frame.place(relwidth=1, relheight=1)
        self.orders_rows_frame = ctk.CTkFrame(
            self.orders_scrollable_frame,
            fg_color="transparent",
        )
        self.orders_rows_frame.pack(fill="x", expand=True)
        self.orders_rows_frame.grid_columnconfigure(0, weight=1)

        self.orders_state_label = ctk.CTkLabel(
            self.orders_table_body,
            text="Загрузка заказов...",
            font=ctk.CTkFont(family="Arial", size=16, weight="bold"),
            text_color=MUTED_COLOR,
            fg_color=SURFACE_COLOR,
            wraplength=620,
            justify="center",
        )
        self.orders_state_label.place(relx=0.5, rely=0.5, anchor="center")

        return page

    # ==============================
    # КАЛЕНДАРЬ ВЫБОРА ДАТЫ
    # ==============================

    def _close_calendar_on_outside_click(self, event):
        popup = self._calendar_popup
        if popup is None or not popup.winfo_exists() or not popup.winfo_ismapped():
            return

        for widget in (popup, self.date_picker_button):
            left = widget.winfo_rootx()
            top = widget.winfo_rooty()
            if (
                left <= event.x_root < left + widget.winfo_width()
                and top <= event.y_root < top + widget.winfo_height()
            ):
                return

        popup.destroy()
        self._calendar_popup = None

    def _toggle_calendar(self):
        if (
            self._calendar_popup is not None
            and self._calendar_popup.winfo_exists()
            and self._calendar_popup.winfo_ismapped()
        ):
            self._calendar_popup.destroy()
            self._calendar_popup = None
            return

        self._calendar_month = self.selected_date.replace(day=1)
        self.update_idletasks()
        x = self.date_picker_button.winfo_rootx()
        y = (
            self.date_picker_button.winfo_rooty()
            + self.date_picker_button.winfo_height()
        )

        self._calendar_popup = ctk.CTkToplevel(self)
        self._calendar_popup.title("Выберите дату")
        self._calendar_popup.geometry(f"286x320+{x}+{y}")
        self._calendar_popup.resizable(False, False)
        self._calendar_popup.transient(self)
        self._render_calendar()

    def _render_calendar(self):
        popup = self._calendar_popup
        if popup is None or not popup.winfo_exists():
            return

        for child in popup.winfo_children():
            child.destroy()

        year = self._calendar_month.year
        month = self._calendar_month.month
        calendar_frame = ctk.CTkFrame(
            popup,
            fg_color=SURFACE_COLOR,
            corner_radius=14,
            border_width=1,
            border_color=BORDER_COLOR,
        )
        calendar_frame.pack(fill="both", expand=True, padx=8, pady=8)
        for column in range(7):
            calendar_frame.grid_columnconfigure(column, weight=1)

        header = ctk.CTkFrame(calendar_frame, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=7, sticky="ew", padx=8, pady=(8, 4))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            header,
            text="‹",
            width=32,
            height=30,
            corner_radius=8,
            fg_color="transparent",
            hover_color="#EEF3EE",
            text_color=TEXT_COLOR,
            command=lambda: self._change_calendar_month(-1),
        ).grid(row=0, column=0)

        ctk.CTkLabel(
            header,
            text=f"{MONTH_NAMES[month - 1]} {year}",
            font=ctk.CTkFont(family="Arial", size=13, weight="bold"),
            text_color=TEXT_COLOR,
        ).grid(row=0, column=1)

        ctk.CTkButton(
            header,
            text="›",
            width=32,
            height=30,
            corner_radius=8,
            fg_color="transparent",
            hover_color="#EEF3EE",
            text_color=TEXT_COLOR,
            command=lambda: self._change_calendar_month(1),
        ).grid(row=0, column=2)

        for column, weekday in enumerate(WEEKDAY_NAMES):
            ctk.CTkLabel(
                calendar_frame,
                text=weekday,
                font=ctk.CTkFont(family="Arial", size=10, weight="bold"),
                text_color=MUTED_COLOR,
                width=32,
                height=24,
            ).grid(row=1, column=column, padx=1)

        for row, week in enumerate(calendar.monthcalendar(year, month), start=2):
            for column, day_number in enumerate(week):
                if day_number == 0:
                    ctk.CTkLabel(
                        calendar_frame,
                        text="",
                        width=32,
                        height=32,
                    ).grid(row=row, column=column, padx=1, pady=1)
                    continue

                selected = date(year, month, day_number) == self.selected_date
                ctk.CTkButton(
                    calendar_frame,
                    text=str(day_number),
                    width=32,
                    height=32,
                    corner_radius=8,
                    fg_color=PRIMARY_COLOR if selected else "transparent",
                    hover_color=PRIMARY_HOVER_COLOR if selected else "#EEF3EE",
                    text_color=SURFACE_COLOR if selected else TEXT_COLOR,
                    font=ctk.CTkFont(family="Arial", size=12),
                    command=lambda day=day_number: self._select_date(
                        date(year, month, day)
                    ),
                ).grid(row=row, column=column, padx=1, pady=1)

    def _change_calendar_month(self, offset):
        year = self._calendar_month.year
        month = self._calendar_month.month + offset
        if month < 1:
            year -= 1
            month = 12
        elif month > 12:
            year += 1
            month = 1

        self._calendar_month = date(year, month, 1)
        self._render_calendar()

    def _select_date(self, selected_day):
        self.selected_date = selected_day
        self.date_picker_button.configure(
            text=selected_day.strftime("%d.%m.%Y") + "   ▾"
        )
        if self._calendar_popup is not None and self._calendar_popup.winfo_exists():
            self._calendar_popup.destroy()
        self._calendar_popup = None
        self.load_orders(selected_day)

    def _open_order_form(self):
        form_panel = self._open_side_panel("Создать заказ", 540)

        form = ctk.CTkScrollableFrame(
            form_panel,
            fg_color=BACKGROUND_COLOR,
            corner_radius=10,
        )
        form.pack(fill="both", expand=True)
        form.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            form,
            text=f"Новый заказ на {self.selected_date.strftime('%d.%m.%Y')}",
            font=ctk.CTkFont(family="Arial", size=20, weight="bold"),
            text_color=TEXT_COLOR,
        ).grid(row=0, column=0, sticky="w", pady=(4, 14))

        def add_entry(row, label, placeholder=""):
            ctk.CTkLabel(
                form,
                text=label,
                font=ctk.CTkFont(family="Arial", size=12),
                text_color=TEXT_COLOR,
            ).grid(row=row, column=0, sticky="w", pady=(5, 3))
            entry = ctk.CTkEntry(form, height=36, placeholder_text=placeholder)
            entry.grid(row=row + 1, column=0, sticky="ew", pady=(0, 4))
            return entry

        recipient_entry = add_entry(1, "Получатель", "Имя получателя")
        phone_entry = add_entry(3, "Номер телефона", "+7 ...")
        ctk.CTkLabel(
            form,
            text="Адрес: Кировская область, Киров,",
            font=ctk.CTkFont(family="Arial", size=12),
            text_color=TEXT_COLOR,
        ).grid(row=5, column=0, sticky="w", pady=(5, 3))
        address_entry = ctk.CTkEntry(
            form,
            height=36,
            placeholder_text="Улица, дом, корпус",
        )
        address_entry.grid(row=6, column=0, sticky="ew", pady=(0, 4))
        floor_entry = add_entry(7, "Этаж")
        apartment_entry = add_entry(9, "Квартира")
        intercom_entry = add_entry(11, "Домофон")
        ctk.CTkLabel(
            form,
            text="Промежуток доставки",
            font=ctk.CTkFont(family="Arial", size=12),
            text_color=TEXT_COLOR,
        ).grid(row=13, column=0, sticky="w", pady=(5, 3))
        time_range = ctk.CTkFrame(form, fg_color="transparent")
        time_range.grid(row=14, column=0, sticky="ew", pady=(0, 4))
        time_range.grid_columnconfigure((0, 2), weight=1)
        time_entry = ctk.CTkEntry(
            time_range,
            height=36,
            placeholder_text="С, например 10:00",
        )
        time_entry.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            time_range,
            text="—",
            font=ctk.CTkFont(size=14),
            text_color=MUTED_COLOR,
        ).grid(row=0, column=1, padx=8)
        time_end_entry = ctk.CTkEntry(
            time_range,
            height=36,
            placeholder_text="По, например 14:00",
        )
        time_end_entry.grid(row=0, column=2, sticky="ew")

        ctk.CTkLabel(
            form,
            text="Комментарий",
            font=ctk.CTkFont(family="Arial", size=12),
            text_color=TEXT_COLOR,
        ).grid(row=15, column=0, sticky="w", pady=(5, 3))
        comment_entry = ctk.CTkTextbox(form, height=80)
        comment_entry.grid(row=16, column=0, sticky="ew", pady=(0, 8))

        error_label = ctk.CTkLabel(
            form,
            text="",
            text_color=ERROR_COLOR,
            wraplength=440,
            justify="left",
        )
        error_label.grid(row=17, column=0, sticky="w", pady=(0, 6))

        def save_order():
            if self.order_store is None:
                error_label.configure(
                    text=f"База данных недоступна: {self.database_error}"
                )
                return

            try:
                delivery_time = time_entry.get().strip()
                delivery_time_end = time_end_entry.get().strip()
                if delivery_time_end and not delivery_time:
                    raise ValueError("Укажите начало промежутка доставки.")
                for value in (delivery_time, delivery_time_end):
                    if value:
                        try:
                            datetime.strptime(value, "%H:%M")
                        except ValueError as error:
                            raise ValueError(
                                "Время укажите в формате ЧЧ:ММ."
                            ) from error
                if delivery_time and delivery_time_end:
                    start_time = datetime.strptime(delivery_time, "%H:%M")
                    end_time = datetime.strptime(delivery_time_end, "%H:%M")
                    if end_time < start_time:
                        raise ValueError(
                            "Время окончания не может быть раньше начала."
                        )

                street_address = address_entry.get()
                if (
                    not recipient_entry.get().strip()
                    or not phone_entry.get().strip()
                    or not street_address.strip()
                ):
                    raise ValueError(
                        "Заполните имя получателя, телефон и адрес."
                    )
                zone_id = None
                latitude = None
                longitude = None
                detection_status = "unavailable"
                address_lookup_error = None
                try:
                    address_data = DadataAPI.check_address(
                        f"{ADDRESS_PREFIX}, {street_address.strip()}"
                    )
                except RuntimeError as error:
                    address_data = None
                    address_lookup_error = str(error)

                if address_data is not None:
                    try:
                        latitude = float(address_data["geo_lat"])
                        longitude = float(address_data["geo_lon"])
                        accuracy = int(address_data["qc_geo"])
                    except (KeyError, TypeError, ValueError):
                        detection_status = "low_accuracy"
                        latitude = None
                        longitude = None
                    else:
                        if accuracy <= 1:
                            try:
                                zone_id, detection_status = (
                                    self.order_store.detect_delivery_zone(
                                        longitude,
                                        latitude,
                                    )
                                )
                            except ValueError:
                                detection_status = "low_accuracy"
                                latitude = None
                                longitude = None
                        else:
                            detection_status = "low_accuracy"

                order_id = self.order_store.create_order(
                    recipient=recipient_entry.get(),
                    phone=phone_entry.get(),
                    street_address=street_address,
                    floor=floor_entry.get(),
                    apartment=apartment_entry.get(),
                    intercom=intercom_entry.get(),
                    order_date=self.selected_date,
                    delivery_time=delivery_time,
                    delivery_time_end=delivery_time_end,
                    comment=comment_entry.get("1.0", "end").strip(),
                    zone_id=zone_id,
                    zone_source="auto" if zone_id is not None else None,
                    zone_detection_status=detection_status,
                    latitude=latitude,
                    longitude=longitude,
                )
            except (ValueError, sqlite3.Error) as error:
                if isinstance(error, sqlite3.Error):
                    self.database_error = error
                    self._set_database_status(error)
                error_label.configure(text=str(error))
                return

            self._close_side_panel()
            self.load_orders()
            if zone_id is None:
                zone_reason = address_lookup_error or {
                    "outside": "Адрес не попал ни в одну зону.",
                    "ambiguous": "Адрес попал на границу или сразу в несколько зон.",
                    "low_accuracy": "DaData не определила адрес с точностью до дома.",
                }.get(detection_status, "Зону нужно выбрать вручную.")
                messagebox.showwarning(
                    "Зона не определена",
                    f"Заказ сохранён. {zone_reason} Откройте его двойным щелчком "
                    "и выберите зону вручную в подробностях.",
                    parent=self,
                )

        buttons = ctk.CTkFrame(form, fg_color="transparent")
        buttons.grid(row=18, column=0, sticky="ew", pady=(4, 8))
        ctk.CTkButton(
            buttons,
            text="Отмена",
            width=110,
            height=38,
            fg_color="#E8ECE8",
            hover_color="#DCE3DC",
            text_color=TEXT_COLOR,
            command=self._close_side_panel,
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            buttons,
            text="Сохранить заказ",
            height=38,
            fg_color=PRIMARY_COLOR,
            hover_color=PRIMARY_HOVER_COLOR,
            text_color=SURFACE_COLOR,
            command=save_order,
        ).pack(side="right")

    # ==============================
    # СТРАНИЦА КУРЬЕРОВ
    # ==============================

    def _create_couriers_page(self):

        page = ctk.CTkFrame(
            self.content_frame, fg_color=BACKGROUND_COLOR, corner_radius=0
        )

        page.place(relwidth=1, relheight=1)

        ctk.CTkLabel(
            page,
            text="Курьеры",
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color=TEXT_COLOR,
        ).pack(anchor="w")

        ctk.CTkLabel(
            page,
            text="Нажмите на имя курьера, чтобы открыть историю доставок и расчёт оплаты.",
            font=ctk.CTkFont(size=12),
            text_color=MUTED_COLOR,
        ).pack(anchor="w", pady=(6, 0))

        form = ctk.CTkFrame(page, fg_color="transparent")
        form.pack(fill="x", pady=(20, 12))

        self.courier_name_entry = ctk.CTkEntry(
            form,
            width=240,
            height=38,
            placeholder_text="Имя курьера",
        )
        self.courier_name_entry.pack(side="left", padx=(0, 8))
        self.courier_phone_entry = ctk.CTkEntry(
            form,
            width=220,
            height=38,
            placeholder_text="Номер телефона",
        )
        self.courier_phone_entry.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            form,
            text="Добавить курьера",
            height=38,
            corner_radius=10,
            fg_color=PRIMARY_COLOR,
            hover_color=PRIMARY_HOVER_COLOR,
            text_color=SURFACE_COLOR,
            command=self._add_courier,
        ).pack(side="left")

        couriers_header = ctk.CTkFrame(page, fg_color="#EEF3EE", corner_radius=8)
        couriers_header.pack(fill="x", pady=(0, 4))
        courier_column_weights = (2.0, 2.0, 2.2, 1.4)
        for column, weight in enumerate(courier_column_weights):
            couriers_header.grid_columnconfigure(
                column,
                weight=int(weight * 10),
                uniform="courier_columns",
            )
        for column, title in enumerate(
            ("Курьер", "Номер телефона", "Telegram-аккаунт", "Подключение")
        ):
            ctk.CTkLabel(
                couriers_header,
                text=title,
                font=ctk.CTkFont(family="Arial", size=12, weight="bold"),
                text_color=TEXT_COLOR,
                anchor="w",
            ).grid(row=0, column=column, sticky="ew", padx=10, pady=10)

        self.courier_column_weights = courier_column_weights
        self.couriers_frame = ctk.CTkScrollableFrame(
            page,
            fg_color=SURFACE_COLOR,
            corner_radius=14,
            border_width=1,
            border_color=BORDER_COLOR,
        )
        self.couriers_frame.pack(fill="both", expand=True, pady=(8, 0))
        return page

    # ==============================
    # СТРАНИЦА ЗОН ДОСТАВОК
    # ==============================

    def _create_zones_page(self):

        page = ctk.CTkFrame(
            self.content_frame, fg_color=BACKGROUND_COLOR, corner_radius=0
        )

        page.place(relwidth=1, relheight=1)

        ctk.CTkLabel(
            page,
            text="Зоны доставок",
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color=TEXT_COLOR,
        ).pack(anchor="w")

        ctk.CTkLabel(
            page,
            text=(
                "Нарисуйте границы зон на карте. Адрес заказа будет "
                "сопоставляться с ними по координатам DaData."
            ),
            font=ctk.CTkFont(size=14),
            text_color=MUTED_COLOR,
            justify="left",
            wraplength=680,
        ).pack(anchor="w", pady=(14, 12))

        controls = ctk.CTkFrame(page, fg_color="transparent")
        controls.pack(anchor="w", pady=(0, 14))
        ctk.CTkButton(
            controls,
            text="Открыть карту зон",
            width=190,
            height=40,
            corner_radius=10,
            fg_color=PRIMARY_COLOR,
            hover_color=PRIMARY_HOVER_COLOR,
            text_color=SURFACE_COLOR,
            command=self._open_zone_map,
        ).pack(side="left")
        ctk.CTkButton(
            controls,
            text="Обновить список",
            width=150,
            height=40,
            corner_radius=10,
            fg_color="#E8ECE8",
            hover_color="#DCE3DC",
            text_color=TEXT_COLOR,
            command=self._refresh_zone_assignments,
        ).pack(side="left", padx=(8, 0))

        zone_header = ctk.CTkFrame(page, fg_color="#EEF3EE", corner_radius=8)
        zone_header.pack(fill="x", pady=(0, 4))
        for column, weight in enumerate((1.1, 2.4, 1.0)):
            zone_header.grid_columnconfigure(
                column,
                weight=int(weight * 10),
                uniform="zone_assignment_columns",
            )
        for column, title in enumerate(("Зона", "Назначенные курьеры", "")):
            ctk.CTkLabel(
                zone_header,
                text=title,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=TEXT_COLOR,
                anchor="w",
            ).grid(row=0, column=column, sticky="ew", padx=10, pady=10)

        self.zone_assignments_frame = ctk.CTkScrollableFrame(
            page,
            fg_color=SURFACE_COLOR,
            corner_radius=14,
            border_width=1,
            border_color=BORDER_COLOR,
        )
        self.zone_assignments_frame.pack(fill="both", expand=True, pady=(4, 0))

        return page

    def _refresh_zone_assignments(self):
        if self.order_store is None:
            return
        try:
            self.couriers = self.order_store.list_couriers()
            zones = self.order_store.list_delivery_zones_with_couriers()
        except sqlite3.Error as error:
            self.database_error = error
            self._set_database_status(error)
            messagebox.showerror(
                "Не удалось загрузить распределение зон",
                str(error),
                parent=self,
            )
            return

        self._render_zone_assignments(zones)

    def _render_zone_assignments(self, zones):
        for child in self.zone_assignments_frame.winfo_children():
            child.destroy()

        if not zones:
            ctk.CTkLabel(
                self.zone_assignments_frame,
                text="Зон пока нет. Создайте их на карте зон.",
                font=ctk.CTkFont(size=14),
                text_color=MUTED_COLOR,
            ).pack(anchor="w", padx=14, pady=16)
            return

        for index, zone in enumerate(zones):
            row = ctk.CTkFrame(
                self.zone_assignments_frame,
                fg_color="#FAFCFA" if index % 2 == 0 else SURFACE_COLOR,
                corner_radius=8,
            )
            row.pack(fill="x", padx=4, pady=3)
            for column, weight in enumerate((1.1, 2.4, 1.0)):
                row.grid_columnconfigure(
                    column,
                    weight=int(weight * 10),
                    uniform="zone_assignment_columns",
                )

            assigned_couriers = zone["couriers"]
            names = ", ".join(str(courier["name"]) for courier in assigned_couriers)
            for column, text in enumerate(
                (
                    str(zone["name"]),
                    names or "Не назначены",
                )
            ):
                ctk.CTkLabel(
                    row,
                    text=text,
                    font=ctk.CTkFont(size=13),
                    text_color=TEXT_COLOR,
                    anchor="w",
                    justify="left",
                    wraplength=480,
                ).grid(row=0, column=column, sticky="ew", padx=10, pady=10)

            ctk.CTkButton(
                row,
                text="Назначить",
                height=32,
                fg_color=PRIMARY_COLOR,
                hover_color=PRIMARY_HOVER_COLOR,
                text_color=SURFACE_COLOR,
                command=lambda zone_id=int(zone["id"]), name=str(zone["name"]): (
                    self._open_zone_courier_assignment(zone_id, name)
                ),
            ).grid(row=0, column=2, sticky="ew", padx=8, pady=6)

    def _open_zone_courier_assignment(self, zone_id: int, zone_name: str):
        panel = self._open_side_panel(f"Курьеры зоны — {zone_name}", 520)
        ctk.CTkLabel(
            panel,
            text="Выберите курьеров, которые будут обслуживать эту зону.",
            font=ctk.CTkFont(size=12),
            text_color=MUTED_COLOR,
            justify="left",
            wraplength=460,
        ).pack(anchor="w", pady=(0, 8))

        if not self.couriers:
            ctk.CTkLabel(
                panel,
                text="Сначала добавьте курьера во вкладке «Курьеры».",
                font=ctk.CTkFont(size=14),
                text_color=TEXT_COLOR,
            ).pack(anchor="w", pady=12)
            return

        courier_frame = ctk.CTkScrollableFrame(
            panel,
            fg_color=BACKGROUND_COLOR,
            corner_radius=10,
        )
        courier_frame.pack(fill="both", expand=True, pady=(0, 10))
        courier_checks: dict[int, ctk.BooleanVar] = {}
        for courier in self.couriers:
            courier_id = int(courier["id"])
            selected = ctk.BooleanVar(value=zone_id in courier["zone_ids"])
            courier_checks[courier_id] = selected
            ctk.CTkCheckBox(
                courier_frame,
                text=f"{courier['name']} — {courier['phone']}",
                variable=selected,
                text_color=TEXT_COLOR,
            ).pack(anchor="w", padx=8, pady=6)

        def save_assignment():
            courier_ids = [
                courier_id
                for courier_id, variable in courier_checks.items()
                if variable.get()
            ]
            try:
                self.order_store.set_zone_couriers(zone_id, courier_ids)
            except (ValueError, sqlite3.Error) as error:
                if isinstance(error, sqlite3.Error):
                    self.database_error = error
                    self._set_database_status(error)
                messagebox.showerror(
                    "Не удалось назначить курьеров",
                    str(error),
                    parent=self,
                )
                return

            self._close_side_panel()
            self._refresh_zone_assignments()
            self.load_orders()

        ctk.CTkButton(
            panel,
            text="Сохранить курьеров зоны",
            height=38,
            fg_color=PRIMARY_COLOR,
            hover_color=PRIMARY_HOVER_COLOR,
            text_color=SURFACE_COLOR,
            command=save_assignment,
        ).pack(fill="x")

    def _open_zone_map(self):
        if self.order_store is None:
            messagebox.showerror(
                "База данных недоступна",
                f"Не удалось открыть карту зон: {self.database_error}",
                parent=self,
            )
            return
        try:
            if self.zone_map_server is None:
                self.zone_map_server = ZoneMapServer(self.order_store)
        except OSError as error:
            messagebox.showerror(
                "Не удалось открыть карту зон",
                str(error),
                parent=self,
            )
            return
        if not webbrowser.open(self.zone_map_server.url):
            messagebox.showerror(
                "Не удалось открыть карту зон",
                "Не удалось открыть браузер. Откройте ссылку вручную:\n"
                f"{self.zone_map_server.url}",
                parent=self,
            )

    # ==============================
    # ПЕРЕКЛЮЧЕНИЕ СТРАНИЦ
    # ==============================

    def show_page(self, page_name):
        if self._side_panel is not None:
            self._close_side_panel()
        self.pages[page_name].tkraise()
        if page_name == "Зоны доставок":
            self._refresh_zone_assignments()

    # ==============================
    # ЗАГРУЗКА ЗАКАЗОВ
    # ==============================

    def load_orders(self, selected_day=None):
        if selected_day is None:
            selected_day = self.selected_date

        self._show_orders_state("Загрузка заказов...")
        if self.order_store is None:
            error = self.database_error or RuntimeError("База данных недоступна.")
            self._set_database_status(error)
            self._show_orders_state(
                f"Не удалось загрузить заказы из базы данных:\n{error}",
                text_color=ERROR_COLOR,
            )
            return

        try:
            self.orders = self.order_store.list_orders(selected_day)
            self.couriers = self.order_store.list_couriers()
        except (OSError, sqlite3.Error) as error:
            self.database_error = error
            self._set_database_status(error)
            self._show_orders_state(
                f"Не удалось загрузить заказы из базы данных:\n{error}",
                text_color=ERROR_COLOR,
            )
            return

        self.database_error = None
        self._set_database_status()
        self._render_orders_table(prepare_orders(self.orders))

    # ==============================
    # ОБРАБОТКА ПОЛУЧЕННЫХ ЗАКАЗОВ
    # ==============================

    def _clear_order_rows(self):
        for row in self.orders_rows_frame.winfo_children():
            row.destroy()

    def _fit_table_label(self, label):
        wraplength = max(label.winfo_width() - 16, 32)
        if label.cget("wraplength") != wraplength:
            label.configure(wraplength=wraplength)

    def _open_side_panel(self, title: str, width: int):
        if self._side_panel is not None and self._side_panel.winfo_exists():
            self._side_panel.destroy()

        panel_width = min(width, max(self.winfo_width() - 270, 320))
        panel_height = max(self.winfo_height() - 48, 320)
        panel = ctk.CTkFrame(
            self,
            width=panel_width,
            height=panel_height,
            fg_color=SURFACE_COLOR,
            corner_radius=14,
            border_width=1,
            border_color=BORDER_COLOR,
        )
        self._side_panel = panel
        self._side_panel_width = width
        panel.place(
            relx=1,
            x=-24,
            y=24,
            anchor="ne",
            relwidth=panel_width / max(self.winfo_width(), 1),
            relheight=0.93,
        )
        panel.lift()

        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(14, 8))
        ctk.CTkLabel(
            header,
            text=title,
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=TEXT_COLOR,
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            header,
            text="×",
            width=34,
            height=34,
            fg_color="transparent",
            hover_color="#EEF3EE",
            text_color=TEXT_COLOR,
            font=ctk.CTkFont(size=20),
            command=self._close_side_panel,
        ).pack(side="right")

        content = ctk.CTkFrame(panel, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        return content

    def _close_side_panel(self):
        if self._side_panel is not None and self._side_panel.winfo_exists():
            self._side_panel.destroy()
        self._side_panel = None

    def _resize_side_panel(self, event):
        if (
            event.widget is self
            and self._side_panel is not None
            and self._side_panel.winfo_exists()
        ):
            panel_width = min(
                self._side_panel_width,
                max(event.width - 270, 320),
            )
            self._side_panel.place_configure(
                relwidth=panel_width / max(event.width, 1),
            )

    def _show_orders_state(self, message, text_color=MUTED_COLOR):
        self._clear_order_rows()
        self.orders_scrollable_frame.place_forget()
        self.orders_state_label.configure(text=message, text_color=text_color)
        self.orders_state_label.place(relx=0.5, rely=0.5, anchor="center")

    def _render_orders_table(self, rows: list[OrderTableRow]):
        self._clear_order_rows()

        if not rows:
            self._show_orders_state("На выбранную дату заказов нет.")
            return

        orders_by_id = {str(item.get("id")): item for item in self.orders}
        for row_index, order in enumerate(rows):
            row_frame = ctk.CTkFrame(
                self.orders_rows_frame,
                fg_color="#FAFCFA" if row_index % 2 == 0 else SURFACE_COLOR,
                corner_radius=8,
            )
            row_frame.pack(fill="x", padx=2, pady=2)
            open_details = lambda _event, order_id=order.order_id: (
                self._open_order_details(order_id)
            )
            row_frame.bind("<Double-Button-1>", open_details)

            for column, weight in enumerate(ORDER_COLUMN_WEIGHTS):
                row_frame.grid_columnconfigure(
                    column,
                    weight=int(weight * 10),
                    uniform="order_columns",
                )

            for column, value in enumerate(order.values):
                if column == len(ORDER_COLUMN_HEADERS) - 1:
                    order_data = orders_by_id.get(str(order.order_id))
                    if order_data is not None:
                        choices, phone_by_label = self._courier_choices_for_order(
                            order_data
                        )
                        current_phone = order_data.get("courier_phone")
                        current_label = next(
                            (
                                label
                                for label, phone in phone_by_label.items()
                                if phone == current_phone
                            ),
                            "— Не назначен —",
                        )
                        courier_menu = ctk.CTkOptionMenu(
                            row_frame,
                            values=list(phone_by_label),
                            variable=ctk.StringVar(value=current_label),
                            width=140,
                            height=32,
                            fg_color=PRIMARY_COLOR,
                            button_color=PRIMARY_HOVER_COLOR,
                            button_hover_color=PRIMARY_HOVER_COLOR,
                            dropdown_fg_color=SURFACE_COLOR,
                            dropdown_text_color=TEXT_COLOR,
                            command=lambda label, item=order_data, mapping=phone_by_label: (
                                self._assign_order_courier(
                                    item,
                                    mapping[label],
                                )
                            ),
                        )
                        courier_menu.grid(
                            row=0,
                            column=column,
                            sticky="ew",
                            padx=6,
                            pady=6,
                        )
                        continue

                cell = ctk.CTkLabel(
                    row_frame,
                    text=value,
                    font=ctk.CTkFont(family="Arial", size=12),
                    text_color=TEXT_COLOR,
                    anchor="w",
                    justify="left",
                    wraplength=250 if column in (3, 7) else 150,
                )
                cell.bind(
                    "<Configure>",
                    lambda _event, label=cell: self._fit_table_label(label),
                )
                cell.bind("<Double-Button-1>", open_details)
                cell.grid(
                    row=0,
                    column=column,
                    sticky="nsew",
                    padx=10,
                    pady=10,
                )

        self.orders_state_label.place_forget()
        self.orders_scrollable_frame.place(relwidth=1, relheight=1)

    def _courier_choices_for_order(self, order, couriers=None):
        zone_id = order.get("zone_id")
        try:
            zone_id = int(zone_id) if zone_id is not None else None
        except (TypeError, ValueError):
            zone_id = None

        couriers = self._couriers_for_zone_first(
            self.couriers if couriers is None else couriers,
            zone_id,
        )
        name_counts: dict[str, int] = {}
        for courier in couriers:
            name = str(courier["name"])
            name_counts[name] = name_counts.get(name, 0) + 1

        choices = {}
        for courier in couriers:
            name = str(courier["name"])
            is_zone_courier = (
                zone_id is not None
                and zone_id in courier.get("zone_ids", [])
            )
            label = (
                f"{name} — {courier['phone']}"
                if name_counts[name] > 1
                else name
            )
            if is_zone_courier:
                label += " · зона"
            choices[label] = str(courier["phone"])
        choices["— Не назначен —"] = None
        return list(choices), choices

    @staticmethod
    def _couriers_for_zone_first(couriers, zone_id):
        return sorted(
            couriers,
            key=lambda courier: (
                zone_id is None or zone_id not in courier.get("zone_ids", []),
                str(courier["name"]).casefold(),
                int(courier["id"]),
            ),
        )

    def _assign_order_courier(self, order, courier_phone):
        if self.order_store is None:
            messagebox.showerror(
                "База данных недоступна",
                str(self.database_error),
                parent=self,
            )
            return
        try:
            self.order_store.update_order_assignment(
                int(order["id"]),
                courier_phone,
                int(order["zone_id"]) if order["zone_id"] is not None else None,
            )
        except (ValueError, LookupError, sqlite3.Error) as error:
            if isinstance(error, sqlite3.Error):
                self.database_error = error
                self._set_database_status(error)
            messagebox.showerror(
                "Не удалось назначить курьера",
                str(error),
                parent=self,
            )
            self.load_orders()
            return

        self.load_orders()

    def _open_order_details(self, order_id):
        if order_id is None:
            return
        if self.order_store is None:
            messagebox.showerror(
                "База данных недоступна",
                str(self.database_error),
                parent=self,
            )
            return
        try:
            order = self.order_store.get_order(int(order_id))
            zones = self.order_store.list_delivery_zones()
            couriers = self.order_store.list_couriers()
        except sqlite3.Error as error:
            self.database_error = error
            self._set_database_status(error)
            messagebox.showerror("Не удалось открыть заказ", str(error), parent=self)
            return
        if order is None:
            messagebox.showerror(
                "Заказ не найден",
                "Обновите список заказов.",
                parent=self,
            )
            return

        details_panel = self._open_side_panel(f"Заказ {order['order_no']}", 500)

        details = ctk.CTkScrollableFrame(
            details_panel,
            fg_color=BACKGROUND_COLOR,
            corner_radius=10,
        )
        details.pack(fill="both", expand=True)
        details.grid_columnconfigure(0, weight=1)

        def add_detail(row, title, value):
            ctk.CTkLabel(
                details,
                text=title,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=MUTED_COLOR,
            ).grid(row=row, column=0, sticky="w", pady=(5, 0))
            ctk.CTkLabel(
                details,
                text=str(value) if value not in (None, "") else "—",
                font=ctk.CTkFont(size=13),
                text_color=TEXT_COLOR,
                justify="left",
                wraplength=440,
            ).grid(row=row + 1, column=0, sticky="ew", pady=(0, 3))

        full_address = (
            f"{ADDRESS_PREFIX}, {order['street_address']}"
            if order["street_address"]
            else "—"
        )
        for row, title, value in (
            (1, "Получатель", order["recipient"]),
            (3, "Телефон получателя", order["phone"]),
            (5, "Адрес", full_address),
            (7, "Этаж", order["floor"]),
            (9, "Квартира", order["apartment"]),
            (11, "Домофон", order["intercom"]),
            (13, "Дата доставки", order["order_date"]),
            (15, "Время доставки", format_delivery_time(order)),
            (17, "Комментарий", order["comment"]),
            (19, "Текущий статус", order["status"]),
        ):
            add_detail(row, title, value)

        detection_labels = {
            "matched": "Зона определена автоматически по DaData",
            "outside": "Адрес не попал ни в одну зону — выберите вручную",
            "ambiguous": "Адрес относится к нескольким зонам — выберите вручную",
            "low_accuracy": "DaData не определила адрес до дома — выберите вручную",
            "unavailable": "Зона не определена — выберите вручную",
            "manual": "Зона выбрана вручную",
            "unassigned": "Зона не выбрана",
        }
        add_detail(
            21,
            "Определение зоны",
            detection_labels.get(
                order["zone_detection_status"],
                "Зона не определена — выберите вручную",
            ),
        )

        zone_options = {"— Не определена —": None}
        zone_names = {}
        for zone in zones:
            zone_options[str(zone["name"])] = int(zone["id"])
            zone_names[int(zone["id"])] = str(zone["name"])
        current_zone_id = order["zone_id"]
        if current_zone_id is not None and int(current_zone_id) not in zone_names:
            inactive_name = str(order.get("zone_name") or "Старая зона")
            inactive_label = f"{inactive_name} (неактивна)"
            zone_options[inactive_label] = int(current_zone_id)
            zone_names[int(current_zone_id)] = inactive_label
        current_zone_label = (
            zone_names.get(int(current_zone_id), "— Не определена —")
            if current_zone_id is not None
            else "— Не определена —"
        )
        ctk.CTkLabel(
            details,
            text="Зона доставки",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=MUTED_COLOR,
        ).grid(row=23, column=0, sticky="w", pady=(8, 3))
        zone_menu = ctk.CTkOptionMenu(
            details,
            values=list(zone_options),
            variable=ctk.StringVar(value=current_zone_label),
            fg_color=PRIMARY_COLOR,
            button_color=PRIMARY_HOVER_COLOR,
            button_hover_color=PRIMARY_HOVER_COLOR,
            dropdown_fg_color=SURFACE_COLOR,
            dropdown_text_color=TEXT_COLOR,
        )
        zone_menu.grid(row=24, column=0, sticky="ew", pady=(0, 8))

        current_zone_id = order["zone_id"]
        courier_values, courier_options = self._courier_choices_for_order(
            {"zone_id": current_zone_id},
            couriers,
        )
        courier_labels = {
            phone: label
            for label, phone in courier_options.items()
            if phone is not None
        }
        current_courier_phone = order["courier_phone"]
        if current_courier_phone:
            current_courier_label = courier_labels.get(
                str(current_courier_phone),
                "— Не назначен —",
            )
        else:
            zone_default = next(
                (
                    label
                    for label in courier_values
                    if label != "— Не назначен —"
                    and " · зона" in label
                ),
                None,
            )
            current_courier_label = zone_default or "— Не назначен —"
        ctk.CTkLabel(
            details,
            text="Назначенный курьер",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=MUTED_COLOR,
        ).grid(row=25, column=0, sticky="w", pady=(8, 3))
        courier_menu = ctk.CTkOptionMenu(
            details,
            values=courier_values,
            variable=ctk.StringVar(value=current_courier_label),
            fg_color=PRIMARY_COLOR,
            button_color=PRIMARY_HOVER_COLOR,
            button_hover_color=PRIMARY_HOVER_COLOR,
            dropdown_fg_color=SURFACE_COLOR,
            dropdown_text_color=TEXT_COLOR,
        )
        courier_menu.grid(row=26, column=0, sticky="ew", pady=(0, 8))

        def update_courier_choices(zone_label):
            previous_phone = courier_options.get(courier_menu.get())
            values, options = self._courier_choices_for_order(
                {"zone_id": zone_options[zone_label]},
                couriers,
            )
            courier_options.clear()
            courier_options.update(options)
            courier_menu.configure(values=values)
            selected = (
                next(
                    (
                        label
                        for label, phone in courier_options.items()
                        if phone == previous_phone
                    ),
                    None,
                )
                if previous_phone is not None
                else None
            )
            if selected is None:
                selected = next(
                    (
                        label
                        for label in values
                        if label != "— Не назначен —" and " · зона" in label
                    ),
                    "— Не назначен —",
                )
            courier_menu.set(selected)

        zone_menu.configure(command=update_courier_choices)

        buttons = ctk.CTkFrame(details, fg_color="transparent")
        buttons.grid(row=27, column=0, sticky="ew", pady=(10, 8))

        def save_details():
            try:
                self.order_store.update_order_assignment(
                    int(order_id),
                    courier_options[courier_menu.get()],
                    zone_options[zone_menu.get()],
                )
            except (ValueError, LookupError, sqlite3.Error) as error:
                if isinstance(error, sqlite3.Error):
                    self.database_error = error
                    self._set_database_status(error)
                messagebox.showerror(
                    "Не удалось сохранить заказ",
                    str(error),
                    parent=self,
                )
                return
            self._close_side_panel()
            self.load_orders()

        ctk.CTkButton(
            buttons,
            text="Закрыть",
            width=110,
            height=38,
            fg_color="#E8ECE8",
            hover_color="#DCE3DC",
            text_color=TEXT_COLOR,
            command=self._close_side_panel,
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            buttons,
            text="Сохранить",
            height=38,
            fg_color=PRIMARY_COLOR,
            hover_color=PRIMARY_HOVER_COLOR,
            text_color=SURFACE_COLOR,
            command=save_details,
        ).pack(side="right")

    def _add_courier(self):
        if self.order_store is None:
            messagebox.showerror(
                "Не удалось добавить курьера",
                f"База данных недоступна: {self.database_error}",
                parent=self,
            )
            return
        try:
            self.order_store.add_courier(
                self.courier_name_entry.get(),
                self.courier_phone_entry.get(),
            )
        except (ValueError, sqlite3.Error) as error:
            if isinstance(error, sqlite3.Error):
                self.database_error = error
                self._set_database_status(error)
            messagebox.showerror("Не удалось добавить курьера", str(error), parent=self)
            return

        self.courier_name_entry.delete(0, "end")
        self.courier_phone_entry.delete(0, "end")
        self.load_orders()
        self._render_couriers_table()

    def _render_couriers_table(self):
        for child in self.couriers_frame.winfo_children():
            child.destroy()

        for courier in self.couriers:
            row = ctk.CTkFrame(
                self.couriers_frame,
                fg_color="transparent",
                corner_radius=8,
            )
            row.pack(fill="x", padx=8, pady=4)
            for column, weight in enumerate(self.courier_column_weights):
                row.grid_columnconfigure(
                    column,
                    weight=int(weight * 10),
                    uniform="courier_columns",
                )

            courier_id = int(courier["id"])
            telegram_username = courier.get("telegram_username")
            telegram_chat_id = courier.get("telegram_chat_id")
            telegram_label = (
                f"@{telegram_username}"
                if telegram_chat_id and telegram_username
                else "Подключён"
                if telegram_chat_id
                else "Не подключён"
            )
            ctk.CTkButton(
                row,
                text=str(courier["name"]),
                fg_color="transparent",
                hover_color="#EEF3EE",
                text_color=TEXT_COLOR,
                anchor="w",
                command=lambda courier_id=courier_id, name=str(courier["name"]): (
                    self._open_courier_history(courier_id, name)
                ),
            ).grid(row=0, column=0, sticky="ew", padx=6, pady=4)

            for column, value in enumerate((courier["phone"], telegram_label), start=1):
                ctk.CTkLabel(
                    row,
                    text=str(value),
                    text_color=TEXT_COLOR,
                    anchor="w",
                    wraplength=200,
                ).grid(row=0, column=column, sticky="ew", padx=10, pady=10)

            ctk.CTkButton(
                row,
                text="Переподключить" if telegram_chat_id else "Подключить",
                height=32,
                corner_radius=8,
                fg_color=PRIMARY_COLOR,
                hover_color=PRIMARY_HOVER_COLOR,
                text_color=SURFACE_COLOR,
                command=lambda courier_id=courier_id: (
                    self._create_telegram_link_code(courier_id)
                ),
            ).grid(row=0, column=3, sticky="ew", padx=8, pady=6)

    @staticmethod
    def _parse_payment_amount(value: str) -> int | None:
        normalized = value.strip().replace("\u00a0", "").replace(" ", "")
        normalized = normalized.replace(",", ".")
        if not normalized:
            return None

        try:
            amount = Decimal(normalized)
        except InvalidOperation as error:
            raise ValueError("Введите сумму числом, например 250 или 250,50.") from error
        if not amount.is_finite() or amount < 0:
            raise ValueError("Сумма должна быть неотрицательным числом.")
        if amount > Decimal("9999999.99"):
            raise ValueError("Максимальная сумма за заказ — 9 999 999,99 ₽.")
        try:
            has_fractional_kopecks = amount != amount.quantize(Decimal("0.01"))
        except InvalidOperation as error:
            raise ValueError(
                "Укажите сумму не более чем с двумя знаками после запятой."
            ) from error
        if has_fractional_kopecks:
            raise ValueError("Укажите не более двух знаков после запятой.")
        return int(amount * 100)

    @staticmethod
    def _format_payment_amount(amount_kopecks: int | None) -> str:
        if amount_kopecks is None:
            return ""
        rubles, kopecks = divmod(amount_kopecks, 100)
        return f"{rubles:,}".replace(",", " ") + f",{kopecks:02d}"

    def _open_courier_history(self, courier_id: int, courier_name: str):
        if self.order_store is None:
            messagebox.showerror(
                "База данных недоступна",
                f"Не удалось открыть историю: {self.database_error}",
                parent=self,
            )
            return

        panel_content = self._open_side_panel(
            f"История доставок — {courier_name}",
            930,
        )

        controls = ctk.CTkFrame(panel_content, fg_color="transparent")
        controls.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            controls,
            text="Дата доставки",
            font=ctk.CTkFont(size=12),
            text_color=MUTED_COLOR,
        ).pack(side="left", padx=(0, 8))
        selected_day = date.today()
        date_entry = ctk.CTkEntry(controls, width=145, height=36)
        date_entry.insert(0, selected_day.strftime("%d.%m.%Y"))
        date_entry.pack(side="left", padx=(0, 8))

        history_orders: list[dict[str, object]] = []
        payment_entries: dict[int, ctk.CTkEntry] = {}
        totals_label = ctk.CTkLabel(
            controls,
            text="",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=TEXT_COLOR,
            anchor="w",
        )
        totals_label.pack(side="left", fill="x", expand=True, padx=8)
        feedback_label = ctk.CTkLabel(
            controls,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=SUCCESS_COLOR,
        )
        feedback_label.pack(side="left", padx=8)

        table_header = ctk.CTkFrame(
            panel_content,
            fg_color="#EEF3EE",
            corner_radius=8,
        )
        table_header.pack(fill="x", pady=(4, 4))
        column_weights = (1.0, 1.4, 2.4, 1.0, 1.2)
        titles = ("Номер заказа", "Получатель", "Адрес", "Сумма, ₽", "Оплата")
        for column, weight in enumerate(column_weights):
            table_header.grid_columnconfigure(
                column,
                weight=int(weight * 10),
                uniform="history_columns",
            )
        for column, title in enumerate(titles):
            ctk.CTkLabel(
                table_header,
                text=title,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=TEXT_COLOR,
                anchor="w",
            ).grid(row=0, column=column, sticky="ew", padx=10, pady=10)

        history_frame = ctk.CTkScrollableFrame(
            panel_content,
            fg_color=SURFACE_COLOR,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_COLOR,
        )
        history_frame.pack(fill="both", expand=True, pady=(0, 8))

        def money_text(amount_kopecks: int) -> str:
            return f"{self._format_payment_amount(amount_kopecks)} ₽"

        def update_totals(_event=None):
            total = 0
            paid = 0
            missing = 0
            invalid = 0
            for order in history_orders:
                entry = payment_entries.get(int(order["id"]))
                if entry is None:
                    continue
                try:
                    amount = self._parse_payment_amount(entry.get())
                except ValueError:
                    invalid += 1
                    continue
                if amount is None:
                    missing += 1
                    continue
                total += amount
                if order["is_paid"]:
                    paid += amount

            unpaid = total - paid
            totals_label.configure(
                text=(
                    f"Всего: {money_text(total)}   "
                    f"Оплачено: {money_text(paid)}   "
                    f"Остаток: {money_text(unpaid)}"
                )
            )
            details = []
            if missing:
                details.append(f"без суммы: {missing}")
            if invalid:
                details.append(f"проверьте ввод: {invalid}")
            feedback_label.configure(
                text=" · ".join(details),
                text_color=ERROR_COLOR if invalid else MUTED_COLOR,
            )

        def render_history():
            for child in history_frame.winfo_children():
                child.destroy()
            payment_entries.clear()

            if not history_orders:
                ctk.CTkLabel(
                    history_frame,
                    text="За выбранную дату доставленных заказов нет.",
                    font=ctk.CTkFont(size=14),
                    text_color=MUTED_COLOR,
                ).pack(pady=24)
                update_totals()
                return

            for row_index, order in enumerate(history_orders):
                row = ctk.CTkFrame(
                    history_frame,
                    fg_color="#FAFCFA" if row_index % 2 == 0 else SURFACE_COLOR,
                    corner_radius=8,
                )
                row.pack(fill="x", padx=4, pady=3)
                for column, weight in enumerate(column_weights):
                    row.grid_columnconfigure(
                        column,
                        weight=int(weight * 10),
                        uniform="history_columns",
                    )

                for column, value in enumerate(
                    (
                        order["order_no"],
                        order["recipient"],
                        order["street_address"],
                    )
                ):
                    ctk.CTkLabel(
                        row,
                        text=str(value or "—"),
                        font=ctk.CTkFont(size=12),
                        text_color=TEXT_COLOR,
                        anchor="w",
                        justify="left",
                        wraplength=250 if column == 2 else 160,
                    ).grid(
                        row=0,
                        column=column,
                        sticky="ew",
                        padx=10,
                        pady=10,
                    )

                order_id = int(order["id"])
                amount_entry = ctk.CTkEntry(
                    row,
                    width=110,
                    height=32,
                    placeholder_text="Например, 250",
                )
                amount_entry.insert(
                    0,
                    self._format_payment_amount(order["delivery_fee_kopecks"]),
                )
                amount_entry.bind("<KeyRelease>", update_totals)
                amount_entry.grid(
                    row=0,
                    column=3,
                    sticky="ew",
                    padx=8,
                    pady=6,
                )
                payment_entries[order_id] = amount_entry

                is_paid = bool(order["is_paid"])
                ctk.CTkButton(
                    row,
                    text="Оплачено" if is_paid else "Не оплачено",
                    height=32,
                    fg_color=PRIMARY_COLOR if is_paid else "#B38732",
                    hover_color=PRIMARY_HOVER_COLOR if is_paid else "#956F28",
                    text_color=SURFACE_COLOR,
                    command=lambda item_id=order_id, paid=not is_paid: (
                        toggle_payment_status(item_id, paid)
                    ),
                ).grid(row=0, column=4, sticky="ew", padx=8, pady=6)

            update_totals()

        def load_history(target_day: date):
            nonlocal selected_day, history_orders
            try:
                history_orders = self.order_store.list_courier_delivery_history(
                    courier_id,
                    target_day,
                )
            except (OSError, sqlite3.Error) as error:
                self.database_error = error
                self._set_database_status(error)
                messagebox.showerror(
                    "Не удалось загрузить историю",
                    str(error),
                    parent=self,
                )
                return

            selected_day = target_day
            date_entry.delete(0, "end")
            date_entry.insert(0, selected_day.strftime("%d.%m.%Y"))
            feedback_label.configure(text="")
            render_history()

        def save_amounts(*, reload: bool = True) -> bool:
            amounts: dict[int, int | None] = {}
            try:
                for order in history_orders:
                    order_id = int(order["id"])
                    amounts[order_id] = self._parse_payment_amount(
                        payment_entries[order_id].get()
                    )
                self.order_store.save_courier_delivery_amounts(
                    courier_id,
                    selected_day,
                    amounts,
                )
            except (ValueError, sqlite3.Error) as error:
                if isinstance(error, sqlite3.Error):
                    self.database_error = error
                    self._set_database_status(error)
                messagebox.showerror(
                    "Не удалось сохранить суммы",
                    str(error),
                    parent=self,
                )
                return False

            for order in history_orders:
                order["delivery_fee_kopecks"] = amounts[int(order["id"])]
            update_totals()
            if reload:
                load_history(selected_day)
            else:
                feedback_label.configure(
                    text="Суммы сохранены",
                    text_color=SUCCESS_COLOR,
                )
            return True

        def toggle_payment_status(order_id: int, is_paid: bool):
            if not save_amounts(reload=False):
                return
            try:
                self.order_store.set_courier_delivery_paid(
                    order_id,
                    courier_id,
                    is_paid,
                )
            except (ValueError, LookupError, sqlite3.Error) as error:
                if isinstance(error, sqlite3.Error):
                    self.database_error = error
                    self._set_database_status(error)
                messagebox.showerror(
                    "Не удалось изменить оплату",
                    str(error),
                    parent=self,
                )
                return
            load_history(selected_day)

        def show_selected_date(_event=None):
            try:
                target_day = datetime.strptime(
                    date_entry.get().strip(),
                    "%d.%m.%Y",
                ).date()
            except ValueError:
                messagebox.showerror(
                    "Некорректная дата",
                    "Введите дату в формате ДД.ММ.ГГГГ.",
                    parent=self,
                )
                return
            if not save_amounts(reload=False):
                return
            load_history(target_day)

        ctk.CTkButton(
            controls,
            text="Показать",
            width=100,
            height=36,
            fg_color=PRIMARY_COLOR,
            hover_color=PRIMARY_HOVER_COLOR,
            text_color=SURFACE_COLOR,
            command=show_selected_date,
        ).pack(side="left", padx=(0, 8))
        date_entry.bind("<Return>", show_selected_date)

        ctk.CTkButton(
            panel_content,
            text="Сохранить суммы",
            height=38,
            fg_color=PRIMARY_COLOR,
            hover_color=PRIMARY_HOVER_COLOR,
            text_color=SURFACE_COLOR,
            command=save_amounts,
        ).pack(fill="x", pady=(0, 2))

        load_history(selected_day)

    def _create_telegram_link_code(self, courier_id):
        if self.order_store is None:
            messagebox.showerror(
                "База данных недоступна",
                f"Не удалось создать код: {self.database_error}",
                parent=self,
            )
            return
        if self.telegram_bot is None:
            messagebox.showerror(
                "Telegram-бот не подключён",
                "Добавьте TELEGRAM_BOT_TOKEN в Config/.env и перезапустите приложение.",
                parent=self,
            )
            return
        try:
            code = self.order_store.create_telegram_link_code(courier_id)
        except (LookupError, sqlite3.Error) as error:
            if isinstance(error, sqlite3.Error):
                self.database_error = error
                self._set_database_status(error)
            messagebox.showerror("Не удалось создать код", str(error), parent=self)
            return

        messagebox.showinfo(
            "Подключение Telegram",
            "Передайте курьеру эту инструкцию:\n\n"
            f"1. Откройте Telegram-бота {self.telegram_username or 'доставки'}.\n"
            "2. Отправьте команду /start.\n"
            "3. Когда бот попросит код, отправьте:\n"
            f"{code}\n\n"
            "Код состоит из 6 цифр, одноразовый и действует 15 минут.",
            parent=self,
        )

    def _start_telegram_bot(self):
        if self.order_store is None:
            self._set_telegram_status(
                "Telegram-бот не запущен: сначала подключите базу данных",
                False,
            )
            return

        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            self._set_telegram_status(
                "Telegram-бот не настроен: добавьте TELEGRAM_BOT_TOKEN в Config/.env",
                False,
            )
            return

        self.telegram_bot = TelegramBot(
            token,
            self.order_store,
            on_courier_linked=self._queue_courier_refresh,
            on_order_updated=self._queue_order_refresh,
        )
        self.telegram_thread = threading.Thread(
            target=self.telegram_bot.run,
            args=(self.telegram_stop_event, self._queue_telegram_status),
            daemon=True,
        )
        self.telegram_thread.start()

    def _queue_telegram_status(self, text, connected):
        if self.telegram_stop_event.is_set():
            return
        self.after(
            0,
            lambda status=text, is_connected=connected: self._set_telegram_status(
                status,
                is_connected,
            ),
        )

    def _queue_courier_refresh(self):
        if not self.telegram_stop_event.is_set():
            self.after(0, self._refresh_couriers)

    def _queue_order_refresh(self):
        if not self.telegram_stop_event.is_set():
            self.after(0, self.load_orders)

    def _refresh_couriers(self):
        if self.order_store is None:
            return
        try:
            self.couriers = self.order_store.list_couriers()
        except sqlite3.Error as error:
            self.database_error = error
            self._set_database_status(error)
            return
        self._render_couriers_table()

    def _set_telegram_status(self, text, connected):
        color = SUCCESS_COLOR if connected else ERROR_COLOR
        self.telegram_status_dot.configure(text_color=color)
        self.telegram_status_label.configure(
            text=text,
            text_color=color,
        )
        if connected and self.telegram_bot:
            self.telegram_username = self.telegram_bot.username

    def _on_close(self):
        self.telegram_stop_event.set()
        if self.zone_map_server is not None:
            self.zone_map_server.close()
        self.destroy()


# ==============================
# ЗАПУСК ПРИЛОЖЕНИЯ
# ==============================

if __name__ == "__main__":

    app = App()
    app.mainloop()
