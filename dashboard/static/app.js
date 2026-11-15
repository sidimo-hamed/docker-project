
const $ = (selector) => document.querySelector(selector);

async function apiRequest(path, options = {}) {
    const response = await fetch(path, options);
    const text = await response.text();

    let data;
    try {
        data = JSON.parse(text);
    } catch {
        data = { message: text };
    }

    if (!response.ok) {
        throw new Error(
            `HTTP ${response.status}\n${JSON.stringify(data, null, 2)}`
        );
    }

    return data;
}

function showOutput(selector, data) {
    $(selector).textContent = JSON.stringify(data, null, 2);
}

async function refreshServices() {
    $("#healthMessage").textContent = "Vérification en cours…";

    try {
        const services = await apiRequest("/api/health");
        const up = services.filter(s => s.status === "UP").length;

        $("#serviceCount").textContent = services.length;
        $("#upCount").textContent = `${up}/${services.length}`;
        $("#overallStatus").textContent =
            up === services.length ? "Tout est actif" :
            up === 0 ? "Indisponible" : "Partiellement actif";

        $("#serviceGrid").innerHTML = services.map(service => {
            const statusClass = service.status === "UP"
                ? "up"
                : service.status === "DOWN" ? "down" : "error";

            return `
                <article class="service-card">
                    <h3>${service.name}</h3>
                    <p>Port : ${service.port}</p>
                    <p>HTTP : ${service.http_status ?? "Aucune réponse"}</p>
                    <span class="status ${statusClass}">
                        ${service.status}
                    </span>
                </article>
            `;
        }).join("");

        $("#healthMessage").textContent =
            "Vérification terminée. Un statut UP signifie que le point de contrôle répond correctement.";
    } catch (error) {
        $("#healthMessage").textContent = error.message;
    }
}

$("#refreshBtn").addEventListener("click", refreshServices);

$("#loadOrdersBtn").addEventListener("click", async () => {
    try {
        showOutput("#ordersOutput", await apiRequest("/api/orders"));
    } catch (error) {
        $("#ordersOutput").textContent = error.message;
    }
});

$("#loadCustomersBtn").addEventListener("click", async () => {
    try {
        showOutput("#dataOutput", await apiRequest("/api/customers"));
    } catch (error) {
        $("#dataOutput").textContent = error.message;
    }
});

$("#loadProductsBtn").addEventListener("click", async () => {
    try {
        showOutput("#dataOutput", await apiRequest("/api/products"));
    } catch (error) {
        $("#dataOutput").textContent = error.message;
    }
});

$("#checkoutForm").addEventListener("submit", async (event) => {
    event.preventDefault();

    const payload = {
        customer_id: Number($("#customerId").value),
        product_id: Number($("#productId").value),
        quantity: Number($("#quantity").value)
    };

    $("#checkoutOutput").textContent = "Traitement de la commande…";

    try {
        const result = await apiRequest("/api/checkout", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        showOutput("#checkoutOutput", result);
        await refreshServices();
        showOutput("#ordersOutput", await apiRequest("/api/orders"));
    } catch (error) {
        $("#checkoutOutput").textContent = error.message;
    }
});

refreshServices();
