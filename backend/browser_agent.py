import os
import sys
import re
import asyncio
import subprocess
import webbrowser

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    async_playwright = None
    PLAYWRIGHT_AVAILABLE = False
    print("[BROWSER AGENT]: playwright not installed — browser automation disabled, falling back to webbrowser.open()")


class PlaywrightBrowserAgent:
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(PlaywrightBrowserAgent, cls).__new__(cls, *args, **kwargs)
            cls._instance.playwright = None
            cls._instance.browser = None
            cls._instance.context = None
            cls._instance.pages = []
            cls._instance.current_page_index = -1
            cls._instance._initialized = False
            cls._instance._init_failed = False
            cls._instance._init_error = ""
            cls._instance._init_retry_count = 0
            # System browser fallback tracking
            cls._instance._using_system_browser = False
            cls._instance._system_browser_window = None
        return cls._instance

    async def initialize(self):
        """Initialize Playwright browser. Handles failures gracefully and prevents
        the singleton from getting stuck in a half-initialized state.
        Includes retry-with-reset: clears stale lock files on first failure and retries once."""
        current_loop = None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        if self._initialized and self.context:
            if hasattr(self, "_loop") and self._loop is not None and self._loop is not current_loop:
                print("[BROWSER AGENT]: Event loop changed, resetting Playwright context...")
                await self._cleanup_partial_init()
            else:
                try:
                    if self.browser and self.browser.is_connected():
                        return True
                except Exception:
                    pass
                print("[BROWSER AGENT]: Browser disconnected, resetting...")
                await self._cleanup_partial_init()
        
        if self._init_failed:
            # Allow one retry per session after clearing stale data
            if self._init_retry_count >= 1:
                return False
            # Try to clear stale lock files and retry
            self._init_retry_count += 1
            self._init_failed = False
            self._init_error = ""
            print("[BROWSER AGENT]: Retrying initialization after clearing stale state...")
            await self._clear_stale_locks()
        
        if not PLAYWRIGHT_AVAILABLE:
            self._init_failed = True
            self._init_error = "playwright module not installed"
            print(f"[BROWSER AGENT]: {self._init_error}")
            return False
        
        try:
            self.playwright = await async_playwright().start()
            
            auth_state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth_state.json")
            
            # Standard Chromium launch (AutoGPT style with --start-maximized)
            try:
                self.browser = await self.playwright.chromium.launch(
                    headless=False,
                    args=["--start-maximized", "--no-sandbox", "--disable-setuid-sandbox"]
                )
                
                # Load persistent auth_state.json if available
                if os.path.exists(auth_state_path):
                    try:
                        self.context = await self.browser.new_context(
                            storage_state=auth_state_path,
                            viewport={"width": 1280, "height": 800}
                        )
                        print(f"[BROWSER AGENT]: Context launched with saved storage_state ({auth_state_path})")
                    except Exception as st_err:
                        print(f"[BROWSER AGENT]: Storage state warning ({st_err}), launching clean context...")
                        self.context = await self.browser.new_context(viewport={"width": 1280, "height": 800})
                else:
                    self.context = await self.browser.new_context(viewport={"width": 1280, "height": 800})
            except Exception as launch_err:
                print(f"[BROWSER AGENT]: Standard launch failed ({launch_err}). Retrying with fallback user data dir...")
                user_data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "playwright_active_session")
                self.context = await self.playwright.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    headless=False,
                    viewport={"width": 1280, "height": 800},
                    args=["--no-sandbox", "--disable-setuid-sandbox"]
                )
            
            self.pages = self.context.pages
            if not self.pages:
                page = await self.context.new_page()
                self.pages = [page]
            self.current_page_index = 0
            self._loop = current_loop
            self._initialized = True
            self._init_failed = False
            self._init_error = ""
            self._using_system_browser = False
            print("[BROWSER AGENT]: Playwright headful browser window initialized successfully!")
            return True
            
        except NotImplementedError as e:
            # This happens when running on the Windows Store Python stub which has
            # a crippled asyncio.subprocess — the most common cause of failure.
            self._init_failed = True
            self._init_error = (
                f"Playwright cannot launch browser: {e}. "
                f"This usually means you're running on the Windows Store Python stub "
                f"(current: {sys.executable}). Use the real Python 3.10 from "
                f"C:\\Users\\nisha\\AppData\\Local\\Programs\\Python\\Python310\\python.exe instead."
            )
            print(f"[BROWSER AGENT]: {self._init_error}")
            await self._cleanup_partial_init()
            return False
            
        except Exception as e:
            self._init_failed = True
            self._init_error = f"Playwright initialization failed: {e}"
            print(f"[BROWSER AGENT]: {self._init_error}")
            await self._cleanup_partial_init()
            return False
    
    async def _cleanup_partial_init(self):
        """Clean up any partially initialized state so the singleton doesn't get stuck."""
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass
        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass
        self.playwright = None
        self.browser = None
        self.context = None
        self.pages = []
        self.current_page_index = -1
        self._initialized = False

    async def _clear_stale_locks(self):
        """Clear stale lock files in the playwright_user_data dir that prevent re-init."""
        user_data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "playwright_user_data")
        if not os.path.isdir(user_data_dir):
            return
        lock_files = [
            os.path.join(user_data_dir, "SingletonLock"),
            os.path.join(user_data_dir, "SingletonCookie"),
            os.path.join(user_data_dir, "SingletonSocket"),
        ]
        for lf in lock_files:
            try:
                if os.path.exists(lf):
                    os.remove(lf)
                    print(f"[BROWSER AGENT]: Removed stale lock file: {lf}")
            except Exception as e:
                print(f"[BROWSER AGENT]: Could not remove lock file {lf}: {e}")

    def is_available(self) -> bool:
        """Check if Playwright browser automation is available and working."""
        return self._initialized and self.context is not None

    def is_using_system_browser(self) -> bool:
        """Check if we fell back to the system browser (no Playwright page API)."""
        return self._using_system_browser

    async def get_current_page(self):
        if not await self.initialize():
            return None
        self.pages = self.context.pages
        if not self.pages:
            page = await self.context.new_page()
            self.pages = [page]
            self.current_page_index = 0
            return page
            
        if self.current_page_index < 0 or self.current_page_index >= len(self.pages):
            self.current_page_index = 0
            
        return self.pages[self.current_page_index]

    async def new_tab(self, url: str = None):
        if not await self.initialize():
            # Three-tier fallback
            if url:
                return self._fallback_open_url_desktop(url) or self._fallback_open_url(url)
            return None
        try:
            page = await self.context.new_page()
            self.pages = self.context.pages
            self.current_page_index = self.pages.index(page)
            if url:
                await page.goto(url, wait_until="load", timeout=30000)
            return page
        except Exception as e:
            print(f"[BROWSER AGENT]: Playwright new_tab failed: {e}")
            if url:
                return self._fallback_open_url_desktop(url) or self._fallback_open_url(url)
            return None

    async def close_tab(self):
        if not await self.initialize():
            return
        self.pages = self.context.pages
        if len(self.pages) <= 1:
            page = await self.get_current_page()
            if page:
                await page.goto("about:blank")
            return
        
        page = await self.get_current_page()
        if page:
            await page.close()
        self.pages = self.context.pages
        self.current_page_index = max(0, self.current_page_index - 1)

    async def switch_tab(self, index_or_title):
        if not await self.initialize():
            return False
        self.pages = self.context.pages
        if isinstance(index_or_title, int):
            if 0 <= index_or_title < len(self.pages):
                self.current_page_index = index_or_title
                await self.pages[self.current_page_index].bring_to_front()
                return True
        else:
            # Match by title or URL substring
            for i, page in enumerate(self.pages):
                try:
                    title = await page.title()
                    url = page.url
                    if index_or_title.lower() in title.lower() or index_or_title.lower() in url.lower():
                        self.current_page_index = i
                        await page.bring_to_front()
                        return True
                except Exception:
                    pass
        return False

    async def navigate(self, url: str):
        if not await self.initialize():
            # Three-tier fallback
            result = self._fallback_open_url_desktop(url)
            if result:
                return result
            return self._fallback_open_url(url)
        try:
            page = await self.get_current_page()
            if page:
                await page.goto(url, wait_until="load", timeout=30000)
                return page
        except Exception as e:
            print(f"[BROWSER AGENT]: Playwright navigate failed: {e}")
            result = self._fallback_open_url_desktop(url)
            if result:
                return result
            return self._fallback_open_url(url)

    async def reload(self):
        page = await self.get_current_page()
        if page:
            await page.reload(wait_until="load")

    async def back(self):
        page = await self.get_current_page()
        if page:
            await page.go_back(wait_until="load")

    async def forward(self):
        page = await self.get_current_page()
        if page:
            await page.go_forward(wait_until="load")

    async def wait(self, seconds: float):
        await asyncio.sleep(seconds)

    async def wait_for_selector(self, selector: str, timeout_ms: int = 10000):
        page = await self.get_current_page()
        if page:
            await page.wait_for_selector(selector, state="visible", timeout=timeout_ms)

    async def wait_for_url(self, url_pattern: str, timeout_ms: int = 15000):
        page = await self.get_current_page()
        if page:
            await page.wait_for_url(url_pattern, timeout=timeout_ms)

    async def click(self, selector: str):
        page = await self.get_current_page()
        if page:
            await page.click(selector, timeout=10000)

    async def double_click(self, selector: str):
        page = await self.get_current_page()
        if page:
            await page.double_click(selector, timeout=10000)

    async def hover(self, selector: str):
        page = await self.get_current_page()
        if page:
            await page.hover(selector, timeout=10000)

    async def type_text(self, selector: str, text: str):
        page = await self.get_current_page()
        if page:
            await page.fill(selector, text, timeout=10000)

    async def clear_input(self, selector: str):
        page = await self.get_current_page()
        if page:
            await page.fill(selector, "", timeout=10000)

    async def press_key(self, key: str):
        page = await self.get_current_page()
        if page:
            await page.keyboard.press(key)

    async def take_screenshot(self, filename: str = None) -> str:
        page = await self.get_current_page()
        if not page:
            return ""
        if not filename:
            import uuid
            filename = f"screenshot_{uuid.uuid4().hex}.png"
        
        screenshot_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "assets")
        os.makedirs(screenshot_dir, exist_ok=True)
        screenshot_path = os.path.join(screenshot_dir, filename)
        await page.screenshot(path=screenshot_path)
        return f"/static/assets/{filename}"

    async def get_browser_status(self) -> dict:
        """Return current browser state. Never crashes — returns empty data on any failure."""
        empty_status = {
            "open_tabs": [],
            "focused_tab_index": -1,
            "current_title": "",
            "current_url": "",
            "playwright_available": PLAYWRIGHT_AVAILABLE,
            "browser_initialized": self._initialized,
            "init_error": self._init_error,
            "using_system_browser": self._using_system_browser
        }
        
        # Don't attempt init just for a status check if we know it will fail
        if self._init_failed or not PLAYWRIGHT_AVAILABLE:
            return empty_status
        
        try:
            if not await self.initialize():
                return empty_status
                
            self.pages = self.context.pages
            tabs_info = []
            for i, page in enumerate(self.pages):
                try:
                    title = await page.title()
                    tabs_info.append({"index": i, "title": title, "url": page.url})
                except Exception:
                    pass
            
            curr_title = ""
            curr_url = ""
            try:
                curr_page = await self.get_current_page()
                if curr_page:
                    curr_title = await curr_page.title()
                    curr_url = curr_page.url
            except Exception:
                pass

            return {
                "open_tabs": tabs_info,
                "focused_tab_index": self.current_page_index,
                "current_title": curr_title,
                "current_url": curr_url,
                "playwright_available": True,
                "browser_initialized": True,
                "init_error": "",
                "using_system_browser": self._using_system_browser
            }
        except Exception as e:
            print(f"[BROWSER AGENT]: Error getting browser status: {e}")
            return empty_status

    async def save_storage_state(self):
        """Saves storage state (cookies, local storage) to auth_state.json for persistent logins."""
        if self.context:
            try:
                auth_state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth_state.json")
                await self.context.storage_state(path=auth_state_path)
                print(f"[BROWSER AGENT]: Saved persistent storage_state to {auth_state_path}")
                return True
            except Exception as e:
                print(f"[BROWSER AGENT]: Failed to save storage_state: {e}")
        return False

    async def find_and_click_element(self, element_description: str) -> dict:
        """Deep DOM & Accessibility Tree Automation:
        1. Uses page.accessibility.snapshot() to locate elements by role/name.
        2. Pierces Shadow DOM roots using page.evaluate().
        3. Returns status dict — signals requires_vision=True ONLY if both DOM strategies fail.
        """
        page = await self.get_current_page()
        if not page:
            return {"success": False, "method": None, "requires_vision": True, "error": "No active page"}
        clean_desc = element_description.strip().lower()
        initial_url = ""
        try:
            initial_url = page.url
        except Exception:
            pass

        # --- Strategy 0: Direct Interactive Element Locator (Links & Buttons First) ---
        try:
            clean_token = re.sub(r'[^\w\s]', '', clean_desc).strip()
            pattern = re.compile(re.escape(clean_token if clean_token else clean_desc), re.IGNORECASE)
            
            # 1. Interactive controls (a, button, input, role=button, role=link) containing target text
            interactive_loc = page.locator("a, button, input, select, textarea, [role='button'], [role='link'], [onclick]").filter(has_text=pattern)
            if await interactive_loc.count() > 0:
                await interactive_loc.first.click(timeout=4000)
                try:
                    await self.save_storage_state()
                except Exception:
                    pass
                return {"success": True, "method": "playwright_interactive_locator"}

            # 2. General text locator fallback (with child/parent link resolution)
            loc = page.get_by_text(pattern)
            if await loc.count() > 0:
                target_el = loc.first
                try:
                    link_target = target_el.locator("xpath=.//a | .//button | ancestor-or-self::a | ancestor-or-self::button")
                    if await link_target.count() > 0:
                        await link_target.first.click(timeout=4000)
                    else:
                        await target_el.click(timeout=4000)
                except Exception:
                    await target_el.click(timeout=4000)
                try:
                    await self.save_storage_state()
                except Exception:
                    pass
                return {"success": True, "method": "playwright_text_locator"}
        except Exception as loc_err:
            err_msg = str(loc_err).lower()
            if any(k in err_msg for k in ["closed", "navigat", "destroy", "timeout", "context", "frame", "detach"]):
                print(f"[DOM AUTOMATION]: Interactive locator triggered page navigation: {loc_err}")
                try:
                    await self.save_storage_state()
                except Exception:
                    pass
                return {"success": True, "method": "playwright_interactive_locator_navigation"}
            print(f"[DOM AUTOMATION]: Interactive locator notice: {loc_err}")

        # --- Strategy 1: Accessibility Tree Snapshot Inspection ---
        if hasattr(page, "accessibility"):
            try:
                acc_obj = getattr(page, "accessibility", None)
                if acc_obj and hasattr(acc_obj, "snapshot"):
                    snapshot = await acc_obj.snapshot()
                    if snapshot:
                        matched_node = self._find_node_in_accessibility_tree(snapshot, clean_desc)
                        if matched_node:
                            role = matched_node.get("role", "")
                            name = matched_node.get("name", "")
                            if name:
                                try:
                                    interactive_roles = {"link", "button", "checkbox", "combobox", "option", "menuitem", "tab", "textbox", "searchbox", "radio", "switch"}
                                    if role and role in interactive_roles:
                                        await page.get_by_role(role, name=name).first.click(timeout=3000)
                                    else:
                                        await page.get_by_text(name).first.click(timeout=3000)
                                    try:
                                        await self.save_storage_state()
                                    except Exception:
                                        pass
                                    return {"success": True, "method": "accessibility_tree", "name": name, "role": role}
                                except Exception as click_err:
                                    err_msg = str(click_err).lower()
                                    if any(k in err_msg for k in ["closed", "navigat", "destroy", "timeout", "context", "frame", "detach"]):
                                        print(f"[DOM AUTOMATION]: Accessibility click triggered page navigation: {click_err}")
                                        try:
                                            await self.save_storage_state()
                                        except Exception:
                                            pass
                                        return {"success": True, "method": "accessibility_tree_navigation", "name": name, "role": role}
                                    pass
            except Exception as acc_err:
                print(f"[DOM AUTOMATION]: Accessibility snapshot notice: {acc_err}")

        # --- Strategy 2: Deep Shadow DOM Piercing via page.evaluate() ---
        try:
            js_code = r"""
            (desc) => {
                const norm = (s) => (s || '').toLowerCase().replace(/…/g, '...').replace(/[^\w\s]/g, ' ').replace(/\s+/g, ' ').trim();
                const target = norm(desc);
                function searchShadow(root) {
                    const selectors = ['a', 'button', 'input', 'select', 'textarea', '[role]', '[onclick]', 'p', 'span', 'h1', 'h2', 'h3', '*'];
                    for (const sel of selectors) {
                        const elements = root.querySelectorAll(sel);
                        for (const el of elements) {
                            const rawTxt = (el.innerText || el.ariaLabel || el.placeholder || el.value || el.title || el.getAttribute('alt') || '');
                            const txt = norm(rawTxt);
                            if (txt && target && (txt.includes(target) || target.includes(txt))) {
                                if (typeof el.click === 'function') {
                                    el.scrollIntoView({behavior: 'smooth', block: 'center'});
                                    el.click();
                                    return { success: true, tag: el.tagName, text: rawTxt.substring(0, 50) };
                                }
                            }
                            if (el.shadowRoot) {
                                const res = searchShadow(el.shadowRoot);
                                if (res.success) return res;
                            }
                        }
                    }
                    return { success: false };
                }
                return searchShadow(document);
            }
            """
            result = await page.evaluate(js_code, clean_desc)
            if result and result.get("success"):
                try:
                    await self.save_storage_state()
                except Exception:
                    pass
                return {"success": True, "method": "shadow_dom_piercing", "details": result}
        except Exception as shadow_err:
            err_msg = str(shadow_err).lower()
            if "execution context was destroyed" in err_msg or "navigat" in err_msg or "target closed" in err_msg:
                print(f"[DOM AUTOMATION]: Element click triggered page navigation: {shadow_err}")
                try:
                    await self.save_storage_state()
                except Exception:
                    pass
                return {"success": True, "method": "shadow_dom_piercing_navigation"}
            print(f"[DOM AUTOMATION]: Shadow DOM evaluation notice: {shadow_err}")

        # --- Strategy 3: Text & Regex Locator Fallbacks ---
        try:
            locator = page.get_by_text(clean_desc)
            if await locator.count() > 0:
                await locator.first.click(timeout=4000)
                await self.save_storage_state()
                return {"success": True, "method": "get_by_text_locator"}
            
            clean_token = re.sub(r'[^\w\s]', '', clean_desc).strip()
            if clean_token:
                regex_loc = page.get_by_text(re.compile(re.escape(clean_token), re.IGNORECASE))
                if await regex_loc.count() > 0:
                    await regex_loc.first.click(timeout=4000)
                    await self.save_storage_state()
                    return {"success": True, "method": "get_by_regex_locator"}
        except Exception as loc_err:
            print(f"[DOM AUTOMATION]: Locator fallback notice: {loc_err}")

        # If all DOM methods fail -> try robust execute_dom_action
        try:
            dom_ok = await execute_dom_action(page, "click", selector=clean_desc)
            if dom_ok:
                await self.save_storage_state()
                return {"success": True, "method": "execute_dom_action_locator"}
        except Exception:
            pass

        # If all DOM methods fail -> fallback to Vision (The Jarvis Way)
        print(f"[DOM AUTOMATION]: DOM selectors failed for '{clean_desc}'. Triggering Vision-based coordinate click fallback...")
        try:
            v_ok = await vision_based_click(page, clean_desc)
            if v_ok:
                return {"success": True, "method": "jarvis_vision_coordinate_click"}
        except Exception as ve:
            print(f"[DOM AUTOMATION]: Vision fallback notice: {ve}")

        return {"success": False, "method": None, "requires_vision": True, "error": "Element not found in DOM or Accessibility tree"}


async def vision_based_click(page, element_description):
    """
    The Jarvis Way: Takes screenshot, asks AI Vision where the element is, clicks X,Y coordinates.
    Use this as a fallback when DOM fails.
    """
    import io
    import tempfile
    import uuid
    from PIL import Image
    import pyautogui

    try:
        if page:
            try:
                await page.bring_to_front()
            except Exception:
                pass
            await asyncio.sleep(1)
            screenshot_bytes = await page.screenshot()
            img = Image.open(io.BytesIO(screenshot_bytes))
        else:
            shot = pyautogui.screenshot()
            img = shot

        width, height = img.size
        
        # Save temp image for vision processing
        temp_screenshot = os.path.join(tempfile.gettempdir(), f"vision_click_{uuid.uuid4().hex[:8]}.png")
        img.save(temp_screenshot)

        gemini_key = os.getenv("GEMINI_API_KEY")
        coords = None
        if gemini_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=gemini_key)
                model = genai.GenerativeModel('gemini-2.0-flash')
                prompt = f"Find the UI element related to '{element_description}'. Return ONLY the bounding box center coordinates as [X, Y] in pixels. Image size is {width}x{height}. Format: X,Y"
                res = model.generate_content([prompt, img])
                coord_text = res.text.strip() if res and res.text else ""
                if ',' in coord_text:
                    parts = coord_text.split(',')
                    x_str, y_str = parts[0], parts[1]
                    x = int(''.join(filter(str.isdigit, x_str)))
                    y = int(''.join(filter(str.isdigit, y_str)))
                    coords = (x, y)
            except Exception as ge:
                print(f"[VISION CLICK]: Gemini Vision call error: {ge}")

        if not coords:
            try:
                from backend.executor import locate_element_via_vision
                coords = await locate_element_via_vision(temp_screenshot, element_description, width, height)
            except Exception as ve:
                print(f"[VISION CLICK]: Executor vision locate fallback notice: {ve}")

        try:
            if os.path.exists(temp_screenshot):
                os.remove(temp_screenshot)
        except Exception:
            pass

        if coords:
            x, y = coords
            print(f"[VISION CLICK]: Moving to ({x}, {y}) and clicking for '{element_description}'")
            pyautogui.moveTo(x, y, duration=0.2)
            pyautogui.click()
            return True
        else:
            print(f"[VISION CLICK]: Could not determine coordinates for '{element_description}'")
            return False

    except Exception as e:
        print(f"Vision Click Failed: {e}")
        return False


async def execute_dom_action(url_or_page, action_type="click", selector=None, text=None, action=None):
    """
    AutoGPT-Style Playwright DOM Action Execution.
    Supports all call signatures:
      - execute_dom_action(url, selector, action, text)
      - execute_dom_action(page, action_type, selector, text)
      - execute_dom_action(page, "click", selector="a")
    """
    page = None
    actual_action = action or action_type or "click"
    actual_selector = selector

    # If calling (url, selector, action, text) positionally where action_type holds selector:
    if isinstance(url_or_page, str):
        url = url_or_page
        if actual_selector is None and isinstance(action_type, str) and action_type not in ["click", "type", "fill"]:
            actual_selector = action_type
            actual_action = "click"
        agent = PlaywrightBrowserAgent()
        if not await agent.initialize():
            return False
        page = await agent.get_current_page()
        if page and url:
            try:
                await page.goto(url)
            except Exception as nav_err:
                print(f"[DOM ACTION]: Navigation notice: {nav_err}")
    else:
        page = url_or_page
        if actual_selector is None and isinstance(action_type, str) and action_type not in ["click", "type", "fill"]:
            actual_selector = action_type
            actual_action = "click"

    if actual_action not in ["click", "type", "fill"]:
        actual_action = "click"

    if not page:
        return False

    try:
        try:
            await page.bring_to_front()
        except Exception:
            pass
        await asyncio.sleep(0.5)

        # Wait for network idle to stabilize elements
        try:
            await page.wait_for_load_state('networkidle', timeout=5000)
        except Exception:
            pass

        if actual_action == "click":
            loc = page.locator(actual_selector).first if (actual_selector and (actual_selector.startswith("#") or actual_selector.startswith(".") or actual_selector.startswith("//") or "[" in actual_selector)) else page.get_by_text(actual_selector).first
            if loc:
                await loc.click(force=True, timeout=5000)
                return True

        elif actual_action in ["type", "fill"]:
            loc = page.locator(actual_selector).first if (actual_selector and (actual_selector.startswith("#") or actual_selector.startswith(".") or actual_selector.startswith("//") or "[" in actual_selector)) else page.get_by_text(actual_selector).first
            if loc:
                await loc.fill("")
                await loc.type(text or "", delay=50)
                return True

    except Exception as e:
        err_str = str(e).lower()
        if "navigat" in err_str or "destroyed" in err_str or "closed" in err_str:
            return True
        print(f"[DOM ACTION]: Locator notice: {e}")

    # FALLBACK: Deep JS Injection for Shadow DOM
    try:
        print("[DOM ACTION]: Attempting Deep Shadow DOM JS Injection fallback...")
        js_script = """
        (args) => {
            const [act, sel, txt] = args;
            const findInShadow = (root) => {
                const allElements = root.querySelectorAll('*');
                for (let el of allElements) {
                    if (el.shadowRoot) {
                        const found = findInShadow(el.shadowRoot);
                        if (found) return found;
                    }
                    if (el.textContent.includes(sel) || el.placeholder?.includes(sel) || el.id?.includes(sel) || el.className?.includes?.(sel)) {
                        if (act === 'click') el.click();
                        if (act === 'type' || act === 'fill') {
                            el.value = txt;
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                        }
                        return true;
                    }
                }
                return false;
            };
            return findInShadow(document);
        }
        """
        result = await page.evaluate(js_script, [actual_action, actual_selector, text])
        if result:
            print("[DOM ACTION]: Deep JS Injection Succeeded!")
            return True
    except Exception as js_e:
        print(f"[DOM ACTION]: JS Injection Failed: {js_e}")

    return False

    def _find_node_in_accessibility_tree(self, node: dict, target_text: str) -> dict:
        """Recursive helper to scan accessibility tree nodes (children first for precise leaf targeting)."""
        if not node or not isinstance(node, dict):
            return None
        children = node.get("children", [])
        for child in children:
            res = self._find_node_in_accessibility_tree(child, target_text)
            if res:
                return res
        name = str(node.get("name", "")).lower()
        value = str(node.get("value", "")).lower()
        desc = str(node.get("description", "")).lower()
        if target_text in name or target_text in value or target_text in desc:
            return node
        return None

    async def shutdown(self):
        try:
            await self.save_storage_state()
            if self.context:
                await self.context.close()
        except Exception:
            pass
        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass
        self.browser = None
        self.context = None
        self.playwright = None
        self.pages = []
        self.current_page_index = -1
        self._initialized = False
        self._using_system_browser = False
        self._system_browser_window = None

    # ---- Fallback Methods ----

    def _fallback_open_url_desktop(self, url: str) -> bool:
        """
        Open a URL using the desktop agent's pywinauto-powered browser launcher.
        This gives us a window handle for subsequent desktop-level interaction,
        unlike plain webbrowser.open(). Sets _using_system_browser flag.
        """
        try:
            from backend.desktop_agent import open_url_in_system_browser
            result = open_url_in_system_browser(url)
            if result["success"]:
                self._using_system_browser = True
                self._system_browser_window = result.get("window")
                print(f"[BROWSER AGENT]: Opened URL via desktop agent ({result['method']}, browser: {result['browser']})")
                return True
        except Exception as e:
            print(f"[BROWSER AGENT]: Desktop agent URL fallback failed: {e}")
        return False
    
    @staticmethod
    def _fallback_open_url(url: str) -> bool:
        """Open a URL using the system's default browser when Playwright is unavailable.
        This always works regardless of Python version or package availability."""
        try:
            print(f"[BROWSER AGENT]: Playwright unavailable, opening URL via system browser: {url}")
            webbrowser.open(url)
            return True
        except Exception as e:
            print(f"[BROWSER AGENT]: webbrowser.open() also failed: {e}")
            # Last resort: try subprocess
            try:
                subprocess.Popen(f'start "" "{url}"', shell=True)
                return True
            except Exception as e2:
                print(f"[BROWSER AGENT]: subprocess start also failed: {e2}")
                return False
    
    def reset_init_state(self):
        """Allow retrying initialization (e.g. after fixing the Python interpreter)."""
        self._init_failed = False
        self._init_error = ""
        self._initialized = False
        self._init_retry_count = 0
        self._using_system_browser = False
        self._system_browser_window = None
