import calendar
import threading
from datetime import date

import customtkinter as ctk
from requests.exceptions import RequestException

from Module.APIrequest import PosiFloraAPI

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
API_SUCCESS_COLOR = "#83D19A"
API_ERROR_COLOR = "#F28B82"
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

        # Создаём объект для работы с Posiflora
        self.api = PosiFloraAPI()

        # Список полученных заказов
        self.orders = []
        self.selected_date = date.today()
        self._orders_request_id = 0
        self._orders_lock = threading.Lock()
        self._api_ever_responded = False
        self._api_check_after_id = None
        self._calendar_popup = None
        self._calendar_month = self.selected_date.replace(day=1)
        self.bind_all("<Button-1>", self._close_calendar_on_outside_click, add="+")

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

        # Список сервисов
        for status in (
            "Подключение к базе данных...",
            "Запуск Telegram-бота...",
        ):

            row = ctk.CTkFrame(status_card, fg_color="transparent")
            row.pack(fill="x", pady=4)

            ctk.CTkLabel(
                row,
                text="●",
                font=ctk.CTkFont(family="Arial", size=9),
                text_color=STATUS_COLOR,
            ).pack(side="left", padx=(0, 7))

            ctk.CTkLabel(
                row,
                text=status,
                font=ctk.CTkFont(family="Arial", size=11),
                text_color=SIDEBAR_TEXT_COLOR,
                anchor="w",
                wraplength=178,
                justify="left",
            ).pack(side="left", fill="x", expand=True)

        api_status_row = ctk.CTkFrame(status_card, fg_color="transparent")
        api_status_row.pack(fill="x", pady=4)

        self.api_status_dot = ctk.CTkLabel(
            api_status_row,
            text="●",
            font=ctk.CTkFont(family="Arial", size=9),
            text_color=STATUS_COLOR,
        )
        self.api_status_dot.pack(side="left", padx=(0, 7))

        self.api_status_label = ctk.CTkLabel(
            api_status_row,
            text="Проверка API...",
            font=ctk.CTkFont(family="Arial", size=11),
            text_color=SIDEBAR_TEXT_COLOR,
            anchor="w",
            wraplength=178,
            justify="left",
        )
        self.api_status_label.pack(side="left", fill="x", expand=True)

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

        self.date_picker_button = ctk.CTkButton(
            date_controls,
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
        self.date_picker_button.pack(anchor="w")

        # Основной блок страницы
        empty_state = ctk.CTkFrame(
            page,
            fg_color=SURFACE_COLOR,
            corner_radius=14,
            border_width=1,
            border_color=BORDER_COLOR,
        )

        empty_state.pack(fill="both", expand=True, pady=(30, 0))

        # Блок сообщения
        message = ctk.CTkFrame(empty_state, fg_color="transparent")

        message.place(relx=0.5, rely=0.5, anchor="center")

        # Текст количества заказов
        self.orders_count = ctk.CTkLabel(
            message,
            text="Загрузка заказов...",
            font=ctk.CTkFont(family="Arial", size=20, weight="bold"),
            text_color=TEXT_COLOR,
        )

        self.orders_count.pack()

        # Дополнительная информация
        self.orders_message = ctk.CTkLabel(
            message,
            text="Ожидаем ответ от Posiflora",
            font=ctk.CTkFont(family="Arial", size=13),
            text_color=MUTED_COLOR,
        )

        self.orders_message.pack(pady=(8, 0))

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

        return page

    # ==============================
    # ПЕРЕКЛЮЧЕНИЕ СТРАНИЦ
    # ==============================

    def show_page(self, page_name):
        # Показываем выбранную страницу
        self.pages[page_name].tkraise()

    # ==============================
    # ЗАГРУЗКА ЗАКАЗОВ
    # ==============================

    def load_orders(self, selected_day=None):

        if selected_day is None:
            selected_day = self.selected_date

        if self._api_check_after_id is not None:
            self.after_cancel(self._api_check_after_id)
            self._api_check_after_id = None

        self.orders_count.configure(text="Загрузка заказов...")
        self.orders_message.configure(text="Ожидаем ответ от Posiflora")
        self._orders_request_id += 1
        request_id = self._orders_request_id

        # Создаём отдельный поток для работы с API
        thread = threading.Thread(
            target=self._get_orders_from_api,
            args=(selected_day, request_id),
            daemon=True,
        )

        thread.start()

    def _schedule_api_check(self):
        if self._api_check_after_id is not None:
            self.after_cancel(self._api_check_after_id)

        self._api_check_after_id = self.after(60_000, self._run_api_check)

    def _run_api_check(self):
        self._api_check_after_id = None
        self.load_orders(self.selected_date)

    def _set_api_status(self, error=None):
        if error is None:
            self._api_ever_responded = True
            self.api_status_dot.configure(text_color=API_SUCCESS_COLOR)
            self.api_status_label.configure(
                text="Доступ к Posiflora получен",
                text_color=API_SUCCESS_COLOR,
            )
            return

        reason = " ".join(str(error).split()) or "Неизвестная ошибка"
        if len(reason) > 120:
            reason = reason[:117] + "..."

        status_text = "API недоступен" if self._api_ever_responded else "Ошибка API"
        self.api_status_dot.configure(text_color=API_ERROR_COLOR)
        self.api_status_label.configure(
            text=f"{status_text}: {reason}",
            text_color=API_ERROR_COLOR,
        )

    # Получение заказов из Posiflora
    def _get_orders_from_api(self, selected_day, request_id):
        with self._orders_lock:
            if request_id != self._orders_request_id:
                return

            try:
                orders = self.api.get_orders(
                    delivery_date=selected_day, pageSize=300, BoolDelivery="true"
                )

                if isinstance(orders, dict):
                    orders = orders.get("data", [])

                self.after(0, lambda: self.on_orders_loaded(orders, None, request_id))

            except (RequestException, RuntimeError) as error:
                error_text = str(error)
                self.after(
                    0, lambda: self.on_orders_loaded(None, error_text, request_id)
                )

    # ==============================
    # ОБРАБОТКА ПОЛУЧЕННЫХ ЗАКАЗОВ
    # ==============================

    def on_orders_loaded(self, orders, error, request_id):
        if request_id != self._orders_request_id:
            return

        # Если произошла ошибка
        if error is not None:

            self.orders_count.configure(text="Ошибка загрузки")

            self.orders_message.configure(text=error)

            self._set_api_status(error)
            self._schedule_api_check()
            return

        # Сохраняем полученные заказы
        self.orders = orders
        self._set_api_status()

        # Выводим количество заказов
        self.orders_count.configure(text=f"Загружено заказов: {len(self.orders)}")

        self.orders_message.configure(text="Данные успешно получены из Posiflora")
        self._schedule_api_check()


# ==============================
# ЗАПУСК ПРИЛОЖЕНИЯ
# ==============================

if __name__ == "__main__":

    app = App()
    app.mainloop()
