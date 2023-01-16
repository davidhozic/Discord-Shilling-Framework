from contextlib import suppress
from random import random

import asyncio



class GLOBALS:
    "Global variables of the web module"
    selenium_installed = False

# ----------------- OPTIONAL ----------------- #
try:
    from undetected_chromedriver import Chrome
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.remote.webelement import WebElement
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.support.wait import WebDriverWait
    from selenium.webdriver.support.expected_conditions import presence_of_element_located
    from selenium.common.exceptions import NoSuchElementException, TimeoutException
    GLOBALS.selenium_installed = True
except:
    GLOBALS.selenium_installed = False
# -------------------------------------------- #


__all__ = (
    "SeleniumCLIENT",
)


WD_TIMEOUT_SHORT = 5
WD_TIMEOUT_MED = 30
WD_TIMEOUT_LONG = 90


class SeleniumCLIENT:
    """
    Client used to control the Discord web client for things such as 
    logging in, joining guilds, passing "Complete" for guild rules.

    This is created in the ACCOUNT object in case ``web`` parameter inside ACCOUNT is True.

    Parameters
    -------------
    username: str
        The Discord username to login with.
    password: str
        The Discord password to login with.
    proxy: str
        The proxy url to use when connecting to Chrome.
    """
    def __init__(self,
                 username: str,
                 password: str,
                 proxy: str) -> None:
        self._username = username
        self._password = password
        opts = Options()
        if proxy is not None:
            opts.add_argument(f"--proxy-server={proxy}")

        self.driver = Chrome(options=opts)
    
    def extract_token(self):
        """
        Get's the token from local storage.
        First it gets the object descriptor that was deleted from Discord.
        """
        driver = self.driver
        token: str =  driver.execute_script(
            """
            const f = document.createElement('iframe');
            document.head.append(f);
            const desc = Object.getOwnPropertyDescriptor(f.contentWindow, 'localStorage');
            f.remove();
            const localStorage = desc.get.call(window);
            return localStorage["token"];
            """
        )
        return token.strip('"').strip("'")

    async def random_sleep(self, bottom: int, upper: int):
        """
        Sleeps randomly to prevent detection.
        """
        await asyncio.sleep(bottom + (upper - bottom)*random())
    
    async def slow_type(self, form: WebElement, text: str):
        """
        Slowly types into a form to prevent detection.
        
        Parameters
        -------------
        form: WebElement
            The form to type ``text`` into.
        text: str
            The text to type in the ``form``.
        """
        await self.await_load()
        actions = ActionChains(self.driver)

        actions.move_to_element(form).perform()
        await self.random_sleep(0.25, 1)
        actions.click(form).perform()

        for char in text:
            form.send_keys(char)
            await self.random_sleep(0.05, 0.10)

    async def await_load(self):
        """
        Waits for the Discord spinning logo to disappear,
        which means that the content has finished loading.

        Raises
        -------------
        TimeoutError
            The page loading timed-out.
        """
        loop = asyncio.get_event_loop()
        await self.random_sleep(1, 2)
        try:
            await loop.run_in_executor(None,
                lambda:
                WebDriverWait(self.driver, WD_TIMEOUT_LONG).until_not(
                    presence_of_element_located((By.XPATH, "//*[@* = 'app-spinner']"))
                )
            )
        except TimeoutException as exc:
            raise TimeoutError(f"Page loading took too long.") from exc

    async def await_captcha(self):
        """
        Waits for CAPTCHA to be completed.

        Raises
        ------------
        TimeoutError
            CAPTCHA was not solved in time.
        """
        loop = asyncio.get_event_loop()
        try:
            # CAPTCHA detected, wait until it is solved by the user
            await asyncio.sleep(WD_TIMEOUT_SHORT)
            await loop.run_in_executor(None, lambda:
                WebDriverWait(self.driver, WD_TIMEOUT_LONG).until_not(
                    presence_of_element_located((By.XPATH, "//iframe[contains(@src, 'captcha')]"))
                )
            )
        except TimeoutException as exc:
            raise TimeoutError(f"CAPTCHA was not solved by the user") from exc

    async def await_two_factor(self):
        """
        Awaits for user to enter 2-factor authorization key.

        Raises
        --------------
        TimeoutError
            2-Factor authentication timed-out.
        """
        loop = asyncio.get_event_loop()
        await asyncio.sleep(WD_TIMEOUT_SHORT)
        try:
            await loop.run_in_executor(None, lambda:
                WebDriverWait(self.driver, WD_TIMEOUT_LONG).until_not(
                    presence_of_element_located((By.XPATH, "//h1[contains(text(), 'Two-factor')]"))
                )
            )
        except TimeoutException as exc:
            raise TimeoutError("2-Factor authentication was not completed in time.") from exc

    async def login(self) -> str:
        """
        Logins to Discord.

        Raises
        ----------
        TimeoutError
            Raised when any of the ``await_*`` methods timed-out.

        Returns
        ----------
        str
            The account's token
        """
        driver = self.driver
        driver.get("https://discord.com/login")
        email_entry = driver.find_element(By.XPATH, "//input[@name='email']")
        pass_entry = driver.find_element(By.XPATH, "//input[@type='password']")
        login_bnt = driver.find_element(By.XPATH, "//button[@type='submit']")

        await self.slow_type(email_entry, self._username)
        await self.slow_type(pass_entry, self._password)
        await self.hover_click(login_bnt)
        await self.await_captcha()
        await self.await_two_factor()
        await self.await_load()
        return self.extract_token()

    async def hover_click(self, element: WebElement):
        """
        Hovers an element and clicks on it.

        Parameters
        -------------
        element: WebElement
            The element to hover click.
        """
        actions = ActionChains(self.driver)
        actions.move_to_element(element).perform()
        await self.random_sleep(0.25, 1)
        actions.click(element).perform()
        await self.random_sleep(1, 2)

    async def join_guild(self, invite: str) -> None:
        """
        Joins the guild thru the browser.

        Parameters
        ------------
        invite: str
            The invite link/code of the guild to join.

        Raises
        ----------
        WebDriverException
            Could not join the guild.
        """
        loop = asyncio.get_event_loop()
        driver = self.driver
        await self.await_load()

        # Join server
        join_bnt = driver.find_element(By.XPATH, "//div[@aria-label='Add a Server']")
        await self.hover_click(join_bnt)

        add_server_bnt = driver.find_element(By.XPATH, "//button[div[text()='Join a Server']]")
        await self.hover_click(add_server_bnt)

        link_input = driver.find_element(By.XPATH, "//input[@type='text']")
        await self.slow_type(link_input, invite)
        await self.random_sleep(2, 5)

        join_bnt = driver.find_element(By.XPATH, "//button[@type='button' and div[contains(text(), 'Join')]]")
        await self.hover_click(join_bnt)
        await self.random_sleep(3, 5) # Wait for any CAPTCHA to appear

        await self.await_captcha()

        with suppress(TimeoutException): 
            await loop.run_in_executor(None, lambda:
                WebDriverWait(driver, WD_TIMEOUT_SHORT).until_not(
                    presence_of_element_located((By.XPATH, "//button[@type='button' and div[contains(text(), 'Join')]]"))
                )
            )

        # Complete rules
        ActionChains(driver).send_keys(Keys.ESCAPE).perform() # To ensure there is not already an open menu
        with suppress(NoSuchElementException):
            complete_rules_bnt = driver.find_element(By.XPATH, "//button[div[contains(text(), 'Complete')]]")
            await self.hover_click(complete_rules_bnt)

        with suppress(NoSuchElementException):
            checkbox = driver.find_element(By.XPATH, "//input[@type='checkbox']")
            await self.hover_click(checkbox)

        with suppress(NoSuchElementException):
            submit_bnt = driver.find_element(By.XPATH, "//button[@type='submit']")
            await self.hover_click(submit_bnt)

