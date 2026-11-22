package local.nkon;

import com.microsoft.playwright.Browser;
import com.microsoft.playwright.BrowserContext;
import com.microsoft.playwright.BrowserType;
import com.microsoft.playwright.Locator;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.Playwright;
import com.microsoft.playwright.Response;
import com.microsoft.playwright.options.AriaRole;
import com.microsoft.playwright.options.WaitUntilState;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

public final class StockMonitor {
    private static final String PRODUCT = "BAK N21700CG 5000 mAh, 10.2 A, NO WRAP";
    private static final String ORIGIN = "https://www.nkon.nl";
    private static final String LOGIN_URL = ORIGIN + "/en/customer/account/login/";
    private static final String UKRAINIAN_ACCOUNT_PATH = "/ua/customer/account/";
    private static final String URL = ORIGIN + "/ua/bak-n21700cg-5000mah-10-2a-no-wrap.html";
    private static final Path STATE = Path.of(".monitor", "stock.txt");
    private static final Path LOCAL_ENV = Path.of(".env");
    private static final Pattern PRODUCT_HEADING =
            Pattern.compile("BAK N21700CG.*5000.*БЕЗ УПАКОВКИ", Pattern.CASE_INSENSITIVE);
    private static final Pattern OUT_OF_STOCK_TEXT =
            Pattern.compile("^(Немає в наявності|Out of stock)$", Pattern.CASE_INSENSITIVE);

    private enum Stock {
        IN_STOCK, OUT_OF_STOCK
    }

    @FunctionalInterface
    private interface StockNotification {
        void send() throws IOException, InterruptedException;
    }

    public static void main(String[] args) throws IOException, InterruptedException {
        if (args.length == 1 && "--simulate-stock".equals(args[0])) {
            simulateStock();
            return;
        }
        if (args.length == 1 && "--telegram-test".equals(args[0])) {
            sendTelegram("Тест NKON: уведомления настроены для " + PRODUCT + "\n" + URL);
            System.out.println("Telegram accepted the test message.");
            return;
        }
        if (args.length != 0 && !(args.length == 1 && "--check-only".equals(args[0]))) {
            throw new IllegalArgumentException("Expected --check-only, --telegram-test, --simulate-stock, or no arguments.");
        }

        Stock current = checkStock();
        System.out.println("NKON stock: " + current);
        if (args.length == 1) {
            return;
        }
        recordStock(STATE, current, () -> {
            sendTelegram("Появился в наличии на NKON: " + PRODUCT + "\n" + URL);
            System.out.println("Telegram accepted the stock notification.");
        });
    }

    private static void recordStock(Path state, Stock current, StockNotification notification)
            throws IOException, InterruptedException {
        Stock previous = Files.exists(state) ? Stock.valueOf(Files.readString(state).strip()) : null;
        if (current == Stock.IN_STOCK && previous != Stock.IN_STOCK) {
            notification.send();
        }
        Files.createDirectories(state.getParent());
        Files.writeString(state, current.name() + "\n");
    }

    // ---------------------------------------------------------------------------
    // Stealth script: patches all browser properties Cloudflare Bot Management
    // uses to fingerprint headless Chromium. Runs before every page load via
    // BrowserContext.addInitScript so it also covers navigations and iframes.
    // ---------------------------------------------------------------------------
    private static final String STEALTH_SCRIPT = """
            // 1. Remove the webdriver flag that every headless browser exposes.
            Object.defineProperty(navigator, 'webdriver', { get: () => false, configurable: true });

            // 2. Spoof a realistic plugins list (PDF viewer + two codecs).
            const makePlugin = (name, filename, description, mimeTypes) => {
                const plugin = Object.create(Plugin.prototype);
                Object.defineProperty(plugin, 'name',        { get: () => name });
                Object.defineProperty(plugin, 'filename',    { get: () => filename });
                Object.defineProperty(plugin, 'description', { get: () => description });
                Object.defineProperty(plugin, 'length',      { get: () => mimeTypes.length });
                mimeTypes.forEach((mt, i) => {
                    const mime = Object.create(MimeType.prototype);
                    Object.defineProperty(mime, 'type',        { get: () => mt.type });
                    Object.defineProperty(mime, 'suffixes',    { get: () => mt.suffixes });
                    Object.defineProperty(mime, 'description', { get: () => mt.description });
                    Object.defineProperty(mime, 'enabledPlugin', { get: () => plugin });
                    Object.defineProperty(plugin, i, { get: () => mime });
                    plugin[mt.type] = mime;
                });
                return plugin;
            };
            const pdfPlugin = makePlugin(
                'PDF Viewer', 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
                'Portable Document Format',
                [
                    { type: 'application/pdf',   suffixes: 'pdf', description: 'Portable Document Format' },
                    { type: 'text/pdf',          suffixes: 'pdf', description: 'Portable Document Format' }
                ]
            );
            const chromePlugin = makePlugin(
                'Chrome PDF Viewer', 'internal-pdf-viewer',
                'Portable Document Format',
                [
                    { type: 'application/pdf',   suffixes: 'pdf', description: 'Portable Document Format' },
                    { type: 'text/pdf',          suffixes: 'pdf', description: 'Portable Document Format' }
                ]
            );
            const naPlugin = makePlugin(
                'Native Client', 'internal-nacl-plugin',
                '',
                [
                    { type: 'application/x-nacl',   suffixes: '', description: 'Native Client Executable' },
                    { type: 'application/x-pnacl',  suffixes: '', description: 'Portable Native Client Executable' }
                ]
            );
            const spoofedPlugins = [pdfPlugin, chromePlugin, naPlugin];
            const pluginArray = Object.create(PluginArray.prototype);
            Object.defineProperty(pluginArray, 'length', { get: () => spoofedPlugins.length });
            spoofedPlugins.forEach((p, i) => {
                Object.defineProperty(pluginArray, i, { get: () => p });
                pluginArray[p.name] = p;
            });
            Object.defineProperty(navigator, 'plugins', { get: () => pluginArray, configurable: true });

            // 3. Spoof languages (match the Ukrainian locale set in BrowserContext).
            Object.defineProperty(navigator, 'languages', { get: () => ['uk-UA', 'uk', 'ru', 'en-US', 'en'], configurable: true });

            // 4. Realistic hardware concurrency & memory (match typical mid-range laptop).
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8, configurable: true });
            if ('deviceMemory' in navigator)
                Object.defineProperty(navigator, 'deviceMemory', { get: () => 8, configurable: true });

            // 5. Spoof permissions query so Cloudflare cannot detect the headless
            //    shortcut that always returns 'denied' for notification permission.
            const _origQuery = window.navigator.permissions.query.bind(navigator.permissions);
            window.navigator.permissions.query = (parameters) => {
                if (parameters.name === 'notifications') {
                    return Promise.resolve({ state: Notification.permission, onchange: null });
                }
                return _origQuery(parameters);
            };

            // 6. Chrome runtime object — absent in headless, present in real Chrome.
            if (!window.chrome) {
                window.chrome = {
                    app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
                           RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } },
                    runtime: {
                        PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
                        PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64', MIPS: 'mips', MIPS64: 'mips64' },
                        RequestUpdateCheckStatus: { THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' },
                        OnInstalledReason: { INSTALL: 'install', UPDATE: 'update', CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update' },
                        OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' }
                    }
                };
            }

            // 7. Realistic screen dimensions (1920×1080, common desktop).
            Object.defineProperty(screen, 'width',       { get: () => 1920, configurable: true });
            Object.defineProperty(screen, 'height',      { get: () => 1080, configurable: true });
            Object.defineProperty(screen, 'availWidth',  { get: () => 1920, configurable: true });
            Object.defineProperty(screen, 'availHeight', { get: () => 1040, configurable: true });
            Object.defineProperty(screen, 'colorDepth',  { get: () => 24,   configurable: true });
            Object.defineProperty(screen, 'pixelDepth',  { get: () => 24,   configurable: true });

            // 8. WebGL renderer strings — headless Chromium reports SwiftShader;
            //    override to a plausible ANGLE/Intel string.
            const _origGetParam = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(param) {
                if (param === 37445) return 'Intel Inc.';                        // UNMASKED_VENDOR_WEBGL
                if (param === 37446) return 'Intel(R) Iris(R) Xe Graphics';     // UNMASKED_RENDERER_WEBGL
                return _origGetParam.call(this, param);
            };
            const _origGetParam2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = function(param) {
                if (param === 37445) return 'Intel Inc.';
                if (param === 37446) return 'Intel(R) Iris(R) Xe Graphics';
                return _origGetParam2.call(this, param);
            };

            // 9. Mask automation-related properties sometimes checked by CF scripts.
            ['$cdc_asdjflasutopfhvcZLmcfl_', '$chrome_asyncScriptInfo',
             '__webdriver_evaluate', '__selenium_evaluate',
             '__webdriver_script_function', '__webdriver_script_func',
             '__webdriver_script_fn', '__fxdriver_evaluate',
             '__driver_unwrapped', '__webdriver_unwrapped',
             '__driver_evaluate', '__selenium_unwrapped',
             '__fxdriver_unwrapped', 'callSelenium',
             '_Selenium_IDE_Recorder', '__selenium_ids'].forEach(prop => {
                try { Object.defineProperty(window, prop, { get: () => undefined, configurable: true }); } catch(e) {}
            });
            """;

    private static Stock checkStock() throws IOException {
        String email = setting("NKON_EMAIL");
        String password = setting("NKON_PASSWORD");
        if (email == null || email.isBlank() || password == null || password.isBlank()) {
            throw new IllegalStateException("Configure NKON_EMAIL and NKON_PASSWORD in .env locally or in GitHub Secrets.");
        }
        boolean headed = "1".equals(System.getenv("NKON_HEADED"));

        // Realistic desktop User-Agent for Chromium shipped with Playwright 1.59
        // (Chromium 133 / Chrome 133 equivalent).
        String userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                + "AppleWebKit/537.36 (KHTML, like Gecko) "
                + "Chrome/133.0.0.0 Safari/537.36";

        try (Playwright playwright = Playwright.create();
             Browser browser = playwright.chromium().launch(
                     new BrowserType.LaunchOptions()
                             .setHeadless(!headed)
                             // Extra flags that reduce the headless surface area
                             .setArgs(List.of(
                                     "--disable-blink-features=AutomationControlled",
                                     "--no-first-run",
                                     "--no-default-browser-check",
                                     "--disable-infobars"
                             )));
             BrowserContext context = browser.newContext(
                     new Browser.NewContextOptions()
                             .setLocale("uk-UA")
                             .setUserAgent(userAgent)
                             .setViewportSize(1920, 1080)
                             .setDeviceScaleFactor(1)
                             .setHasTouch(false)
                             .setJavaScriptEnabled(true))) {

            // Inject the stealth patches before any page load.
            context.addInitScript(STEALTH_SCRIPT);

            Page page = context.newPage();
            try {
                return checkProduct(page, email, password);
            } catch (RuntimeException failure) {
                if (headed) {
                    System.err.println(failure.getMessage());
                    if (failure.getMessage() != null && failure.getMessage().startsWith("NKON returned HTTP 403")
                            && page.locator("form input[type='password']:visible").count() == 0) {
                        page.screenshot(new Page.ScreenshotOptions().setPath(Path.of(".monitor", "nkon-403.png")));
                    }
                    System.err.println("Chromium will stay open for 90 seconds for inspection.");
                    page.waitForTimeout(90_000);
                }
                throw failure;
            }
        }
    }

    private static Stock checkProduct(Page page, String email, String password) {
        signInAndSwitchLanguage(page, email.strip(), password);
        openPage(page, URL, "product page");
        requirePath(page, URI.create(URL).getPath(), "Ukrainian product page");
        return detectStock(page);
    }

    private static Stock detectStock(Page page) {
        page.getByRole(AriaRole.HEADING, new Page.GetByRoleOptions()
                .setLevel(1).setName(PRODUCT_HEADING)).waitFor();
        Locator outOfStock = page.getByText(OUT_OF_STOCK_TEXT).filter(new Locator.FilterOptions().setVisible(true));
        Locator addToCart = page.locator("button#product-addtocart-button:visible")
                .or(page.getByRole(AriaRole.BUTTON, new Page.GetByRoleOptions().setName(
                        Pattern.compile("^(Додати (до|в) кошик|У кошик|Add to Cart)$",
                                Pattern.CASE_INSENSITIVE))));
        outOfStock.or(addToCart).first().waitFor();

        boolean unavailable = outOfStock.first().isVisible();
        boolean purchaseButton = addToCart.first().isVisible();
        if (unavailable == purchaseButton) {
            throw new IllegalStateException("NKON stock signals are missing or contradictory.");
        }
        return unavailable ? Stock.OUT_OF_STOCK : Stock.IN_STOCK;
    }

    private static void simulateStock() throws IOException, InterruptedException {
        Path directory = Files.createTempDirectory("nkon-stock-simulation-");
        Path state = directory.resolve("stock.txt");
        List<Stock> notifications = new ArrayList<>();
        try (Playwright playwright = Playwright.create();
             Browser browser = playwright.chromium().launch()) {
            Page page = browser.newPage();
            for (Stock expected : List.of(Stock.OUT_OF_STOCK, Stock.IN_STOCK, Stock.IN_STOCK)) {
                String stockMarkup = expected == Stock.OUT_OF_STOCK
                        ? "<p>Немає в наявності</p>"
                        : "<button id='product-addtocart-button'>Додати до кошик</button>";
                page.setContent("<h1>BAK N21700CG 5000mAh - 10.2A - БЕЗ УПАКОВКИ</h1>" + stockMarkup);
                Stock actual = detectStock(page);
                if (actual != expected) {
                    throw new IllegalStateException("Expected " + expected + ", detected " + actual);
                }
                recordStock(state, actual, () -> notifications.add(actual));
                System.out.println(actual + " | notifications: " + notifications.size());
            }
            if (!notifications.equals(List.of(Stock.IN_STOCK))) {
                throw new IllegalStateException("Expected exactly one in-stock notification.");
            }
        } finally {
            Files.deleteIfExists(state);
            Files.deleteIfExists(directory);
        }
    }

    private static void signInAndSwitchLanguage(Page page, String email, String password) {
        openPage(page, LOGIN_URL, "sign-in page");
        Locator form = page.locator("form#login-form, form[action*='/customer/account/loginPost']")
                .filter(new Locator.FilterOptions().setVisible(true));
        form.locator("input[name='login[username]']").fill(email);
        form.locator("input[name='login[password]']").fill(password);
        Response submitted = page.waitForResponse(response -> response.request().method().equals("POST")
                        && isNkonUrl(URI.create(response.url()))
                        && URI.create(response.url()).getPath().matches("(?i).*/customer/account/loginpost/?"),
                () -> form.locator("button[type='submit']:visible, input[type='submit']:visible").click());
        if (submitted.status() >= 400) {
            throw new IllegalStateException("NKON returned HTTP " + submitted.status() + " after sign-in.");
        }

        openPage(page, ORIGIN + UKRAINIAN_ACCOUNT_PATH + "?language_switch=1", "Ukrainian account page");
        requirePath(page, UKRAINIAN_ACCOUNT_PATH, "authenticated Ukrainian account page");
        System.out.println("NKON signed in and switched to the Ukrainian storefront.");
    }

    private static void openPage(Page page, String url, String description) {
        Response response = page.navigate(url, new Page.NavigateOptions()
                .setWaitUntil(WaitUntilState.DOMCONTENTLOADED).setTimeout(45_000));
        if (response == null) {
            throw new IllegalStateException("NKON returned no response for the " + description + ".");
        }
        if (!response.ok()) {
            throw new IllegalStateException("NKON returned HTTP " + response.status() + " for the " + description + ".");
        }
        if (!isNkonUrl(URI.create(page.url()))) {
            throw new IllegalStateException("NKON redirected outside its storefront.");
        }
    }

    private static boolean isNkonUrl(URI url) {
        return "https".equals(url.getScheme()) && "www.nkon.nl".equals(url.getHost())
                && (url.getPort() == -1 || url.getPort() == 443);
    }

    private static void requirePath(Page page, String expected, String description) {
        String actual = URI.create(page.url()).getPath();
        if (!actual.equals(expected) && !(expected.endsWith("/") && (actual + "/").equals(expected))) {
            throw new IllegalStateException("NKON did not open the " + description + ".");
        }
    }

    private static String setting(String name) throws IOException {
        String value = System.getenv(name);
        if (value != null || System.getenv("GITHUB_ACTIONS") != null || !Files.isRegularFile(LOCAL_ENV)) {
            return value;
        }
        String prefix = name + "=";
        for (String line : Files.readAllLines(LOCAL_ENV)) {
            if (line.startsWith(prefix)) {
                return line.substring(prefix.length());
            }
        }
        return null;
    }

    private static void sendTelegram(String message) throws IOException, InterruptedException {
        String token = setting("TELEGRAM_BOT_TOKEN");
        String chatId = setting("TELEGRAM_CHAT_ID");
        if (token == null || !token.matches("[0-9]+:[A-Za-z0-9_-]+")
                || chatId == null || !chatId.matches("-?[0-9]+")) {
            throw new IllegalStateException("Configure TELEGRAM_BOT_TOKEN and numeric TELEGRAM_CHAT_ID.");
        }

        String body = "chat_id=" + chatId + "&text=" + URLEncoder.encode(message, StandardCharsets.UTF_8);
        HttpRequest request = HttpRequest.newBuilder(URI.create("https://api.telegram.org/bot" + token + "/sendMessage"))
                .timeout(Duration.ofSeconds(20))
                .header("Content-Type", "application/x-www-form-urlencoded")
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();
        HttpResponse<Void> response;
        try (HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build()) {
            try {
                response = client.send(request, HttpResponse.BodyHandlers.discarding());
            } catch (IOException failure) {
                throw new IOException("Could not reach the Telegram Bot API.");
            }
        }
        if (response.statusCode() != 200) {
            throw new IOException("Telegram rejected sendMessage (HTTP " + response.statusCode() + ").");
        }
    }
}
