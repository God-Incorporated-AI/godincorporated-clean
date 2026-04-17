document.addEventListener("DOMContentLoaded", async () => {
  const menuToggle = document.getElementById("menuToggle");
  const mainMenu = document.getElementById("mainMenu");
  const menuAnonymous = document.getElementById("menuAnonymous");
  const menuAuthenticated = document.getElementById("menuAuthenticated");
  const userDisplayName = document.getElementById("userDisplayName");
  const logoutBtn = document.getElementById("logoutBtn");
  const adminNavBtn = document.getElementById("adminNavBtn");
  const supportBtnAnonymous = document.getElementById("supportBtnAnonymous");
  const supportBtnAuthenticated = document.getElementById("supportBtnAuthenticated");

  if (!menuToggle || !mainMenu) return;

  function safeShow(el, displayValue = "flex") {
    if (!el) return;
    el.hidden = false;
    el.style.display = displayValue;
  }

  function safeHide(el) {
    if (!el) return;
    el.hidden = true;
    el.style.display = "none";
  }

  function setAuthenticatedMenuState(isAuthenticated) {
    if (isAuthenticated) {
      safeHide(menuAnonymous);
      safeShow(menuAuthenticated, "flex");
    } else {
      safeShow(menuAnonymous, "flex");
      safeHide(menuAuthenticated);
    }
  }

  function updateMenuToggleIdentity(identity) {
    if (!menuToggle) return;
    if (identity && identity.authenticated) {
      const fullName = (identity.display_name || "Account").trim();
      const shortName = fullName.length > 14 ? fullName.slice(0, 14) + "..." : fullName;
      menuToggle.textContent = shortName + " ▾";
      menuToggle.setAttribute("aria-label", "Open account menu for " + fullName);
      menuToggle.title = fullName;
    } else {
      menuToggle.textContent = "☰";
      menuToggle.setAttribute("aria-label", "Open menu");
      menuToggle.removeAttribute("title");
    }
  }

  function navigateTo(path) {
    window.location.href = path;
  }

  document.querySelectorAll("[data-nav]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const path = btn.getAttribute("data-nav");
      if (path) navigateTo(path);
    });
  });

  if (supportBtnAnonymous) {
    supportBtnAnonymous.addEventListener("click", () => navigateTo("/temple?support=1"));
  }

  if (supportBtnAuthenticated) {
    supportBtnAuthenticated.addEventListener("click", () => navigateTo("/temple?support=1"));
  }

  if (logoutBtn) {
    logoutBtn.addEventListener("click", async () => {
      try {
        await fetch("/auth/logout", {
          method: "POST",
          credentials: "same-origin"
        });
      } catch (err) {
        console.error("Logout failed:", err);
      } finally {
        navigateTo("/temple");
      }
    });
  }

  menuToggle.addEventListener("click", (e) => {
    e.stopPropagation();
    mainMenu.classList.toggle("show");
  });

  document.addEventListener("click", (e) => {
    if (!menuToggle.contains(e.target) && !mainMenu.contains(e.target)) {
      mainMenu.classList.remove("show");
    }
  });

  try {
    const response = await fetch("/me", { credentials: "same-origin" });
    const data = await response.json();

    if (!response.ok || !data.authenticated) {
      setAuthenticatedMenuState(false);
      updateMenuToggleIdentity(null);
      if (adminNavBtn) {
        adminNavBtn.hidden = true;
        adminNavBtn.style.display = "none";
      }
      return;
    }

    setAuthenticatedMenuState(true);
    updateMenuToggleIdentity(data);

    if (userDisplayName) {
      userDisplayName.textContent = data.display_name || "";
    }

    const isAdmin = ["admin", "owner"].includes(data.role);
    if (adminNavBtn) {
      adminNavBtn.hidden = !isAdmin;
      adminNavBtn.style.display = isAdmin ? "" : "none";
    }
  } catch (err) {
    console.error("Failed to load site shell identity:", err);
    setAuthenticatedMenuState(false);
    updateMenuToggleIdentity(null);
    if (adminNavBtn) {
      adminNavBtn.hidden = true;
      adminNavBtn.style.display = "none";
    }
  }
});
