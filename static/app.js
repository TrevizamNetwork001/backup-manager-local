(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const request = input instanceof Request ? input : null;
    const url = new URL(request?.url || String(input), window.location.href);
    const method = String(init.method || request?.method || "GET").toUpperCase();
    if (csrfToken && url.origin === window.location.origin && !["GET", "HEAD", "OPTIONS"].includes(method)) {
      const headers = new Headers(request?.headers || undefined);
      new Headers(init.headers || undefined).forEach((value, key) => headers.set(key, value));
      headers.set("X-CSRF-Token", csrfToken);
      init = {...init, headers};
    }
    return nativeFetch(input, init);
  };
  if (document.querySelector("[data-system-refresh]")) {
    window.setTimeout(() => window.location.reload(), 60000);
  }
  const connectionMethod = document.querySelector("[data-connection-method]");
  const accessPort = document.querySelector("[data-access-port]");
  connectionMethod?.addEventListener("change", () => {
    if (!accessPort) return;
    const current = accessPort.value.trim();
    const usesTelnet = connectionMethod.value === "fiberhome_olt_telnet_ftp" ||
      connectionMethod.value === "vsol_olt_telnet_cli";
    if (usesTelnet && (current === "" || current === "22")) {
      accessPort.value = "23";
    } else if (!usesTelnet && current === "23") {
      accessPort.value = "22";
    }
  });
  const sidebar = document.querySelector(".sidebar");
  const menuToggle = document.querySelector(".menu-toggle");
  const currentPath = window.location.pathname;
  document.querySelectorAll(".sidebar nav a").forEach((link) => {
    const path = new URL(link.href).pathname;
    const active = path === "/" ? currentPath === "/" : currentPath === path || currentPath.startsWith(path + "/");
    if (active) link.classList.add("active");
  });
  menuToggle?.addEventListener("click", () => {
    const open = sidebar?.classList.toggle("is-open") || false;
    menuToggle.setAttribute("aria-expanded", String(open));
  });
  const accountMenu = document.querySelector(".topbar-account-menu");
  document.addEventListener("click", (event) => {
    if (accountMenu?.open && !accountMenu.contains(event.target)) accountMenu.open = false;
  });
  accountMenu?.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      accountMenu.open = false;
      accountMenu.querySelector(":scope > summary")?.focus();
    }
  });
  document.querySelectorAll("[data-secret-toggle]").forEach((button) => button.addEventListener("click", () => {
    const input = button.parentElement?.querySelector("input");
    if (!input) return;
    input.type = input.type === "password" ? "text" : "password";
    const visible = input.type === "text";
    button.setAttribute("aria-label", visible ? "Ocultar token" : "Mostrar token");
    button.classList.toggle("visible", visible);
  }));
  document.querySelectorAll("[data-topic-mapping-form]").forEach((form) => {
    const type = form.querySelector("[data-topic-mapping-type]");
    const labels = {
      method: ["Método de backup", "Ex.: ssh, ftp ou mikrotik"],
      category: ["Categoria da mensagem", "Ex.: alerts ou backup"],
      event: ["Evento específico", "Ex.: backup_completed"],
    };
    const update = () => {
      const selected = type?.value || "equipment";
      form.querySelectorAll("[data-topic-field]").forEach((field) => {
        const visible = field.dataset.topicField === selected || (field.dataset.topicField === "value" && selected in labels);
        field.hidden = !visible;
        field.querySelectorAll("input, select").forEach((input) => { input.disabled = !visible; });
      });
      const [label, help] = labels[selected] || ["Valor da regra", "Use o identificador operacional configurado no sistema."];
      const labelTarget = form.querySelector("[data-topic-value-label]");
      const helpTarget = form.querySelector("[data-topic-value-help]");
      if (labelTarget) labelTarget.textContent = label;
      if (helpTarget) helpTarget.textContent = help;
    };
    type?.addEventListener("change", update);
    update();
  });
  document.querySelectorAll("[data-telegram-policy-form]").forEach((form) => {
    const scope = form.querySelector("[data-policy-scope]");
    const allFiles = form.querySelector("[data-policy-all-files]");
    const compression = form.querySelector("[data-policy-compression]");
    const update = () => {
      form.querySelectorAll("[data-policy-field]").forEach((field) => {
        const visible = field.dataset.policyField === scope?.value;
        field.hidden = !visible;
        field.querySelectorAll("input, select").forEach((input) => { input.disabled = !visible; });
      });
      const types = form.querySelector("[data-policy-file-types]");
      if (types) { types.hidden = Boolean(allFiles?.checked); types.querySelector("input")?.toggleAttribute("disabled", Boolean(allFiles?.checked)); }
      const level = form.querySelector("[data-policy-compression-level]");
      if (level) { level.hidden = compression?.value === "none"; level.querySelector("input")?.toggleAttribute("disabled", compression?.value === "none"); }
    };
    scope?.addEventListener("change", update);
    allFiles?.addEventListener("change", update);
    compression?.addEventListener("change", update);
    form.addEventListener("reset", () => window.setTimeout(update));
    update();
  });
  document.querySelectorAll("[data-telegram-items-page]").forEach((page) => {
    const search = page.querySelector("[data-telegram-item-search]");
    const status = page.querySelector("[data-telegram-item-status]");
    const rows = [...page.querySelectorAll("[data-telegram-item]")];
    const refresh = () => {
      const query = (search?.value || "").trim().toLocaleLowerCase("pt-BR");
      let visible = 0;
      rows.forEach((row) => {
        const show = (!query || (row.dataset.search || "").includes(query)) && (!status?.value || row.dataset.status === status.value);
        row.hidden = !show;
        if (show) visible += 1;
      });
      const empty = page.querySelector("[data-telegram-no-results]");
      if (empty) empty.hidden = visible > 0 || rows.length === 0;
    };
    search?.addEventListener("input", refresh);
    status?.addEventListener("change", refresh);
  });

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-dialog-open]");
    if (!button) return;
    const dialog = document.getElementById(button.dataset.dialogOpen || "");
    if (dialog && !dialog.open) dialog.showModal();
  });
  document.querySelectorAll("dialog[data-dialog-auto-open]").forEach((dialog) => dialog.showModal());
  document.querySelectorAll("[data-dialog-close]").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog")?.close());
  });

  const equipmentRows = [...document.querySelectorAll("[data-equipment-row]")];
  const equipmentFilters = [...document.querySelectorAll("[data-equipment-filter]")];
  const equipmentSearch = document.querySelector("[data-equipment-search]");
  if (equipmentRows.length) {
    const refreshEquipmentList = () => {
      const query = (equipmentSearch?.value || "").trim().toLocaleLowerCase("pt-BR");
      let visible = 0;
      equipmentRows.forEach((row) => {
        const matchesFilters = equipmentFilters.every((filter) => !filter.value || row.dataset[filter.dataset.equipmentFilter || ""] === filter.value);
        const matchesSearch = !query || (row.dataset.search || "").includes(query);
        row.hidden = !(matchesFilters && matchesSearch);
        if (!row.hidden) visible += 1;
      });
      const count = document.querySelector("[data-equipment-visible]");
      const empty = document.querySelector(".equipment-empty");
      if (count) count.textContent = String(visible);
      if (empty) empty.hidden = visible !== 0;
    };
    equipmentFilters.forEach((filter) => filter.addEventListener("change", refreshEquipmentList));
    equipmentSearch?.addEventListener("input", refreshEquipmentList);
  }

  const ftpWizard = document.querySelector("[data-ftp-wizard]");
  if (ftpWizard) {
    const form = ftpWizard.querySelector(".ftp-wizard-form");
    const panels = [...ftpWizard.querySelectorAll("[data-ftp-step]")];
    const indicators = [...ftpWizard.querySelectorAll("[data-ftp-step-indicator]")];
    const previous = ftpWizard.querySelector("[data-ftp-previous]");
    const next = ftpWizard.querySelector("[data-ftp-next]");
    const submit = ftpWizard.querySelector("[data-ftp-submit]");
    const accountType = form?.elements.namedItem("account_type");
    const equipment = form?.elements.namedItem("equipment_id");
    const accountName = form?.elements.namedItem("name");
    const username = form?.elements.namedItem("username");
    const permission = form?.elements.namedItem("permission_mode");
    const quota = form?.elements.namedItem("quota_bytes");
    const uploadSubdirectory = form?.elements.namedItem("upload_subdirectory");
    const password = form?.elements.namedItem("password");
    const confirmation = form?.elements.namedItem("confirm_password");
    const passwordStrength = ftpWizard.querySelector("[data-ftp-password-strength]");
    let step = 1;

    const valueLabel = (field, fallback = "-") => field?.dataset?.label || field?.selectedOptions?.[0]?.textContent?.trim() || field?.value?.trim() || fallback;
    const setSummary = (key, value) => {
      const target = ftpWizard.querySelector(`[data-ftp-summary="${key}"]`);
      if (target) target.textContent = value || "-";
    };
    const updatePurpose = () => {
      const standalone = accountType?.value === "file_server";
      const equipmentField = ftpWizard.querySelector("#ftp-equipment-field");
      const nameField = ftpWizard.querySelector("#ftp-name-field");
      if (equipmentField) equipmentField.hidden = standalone;
      if (nameField) nameField.hidden = !standalone;
      if (equipment) equipment.required = !standalone;
      if (accountName) accountName.required = standalone;
    };
    const updateSummary = () => {
      updatePurpose();
      const standalone = accountType?.value === "file_server";
      setSummary("purpose", valueLabel(accountType));
      setSummary("equipment", standalone ? "Sem vínculo" : valueLabel(equipment));
      setSummary("name", standalone ? accountName?.value.trim() : valueLabel(equipment));
      setSummary("username", username?.value.trim());
      setSummary("permission", valueLabel(permission));
      setSummary("quota", quota?.value ? `${quota.value} bytes` : "Sem limite");
      setSummary("folder", uploadSubdirectory?.value.trim() ? `/${uploadSubdirectory.value.trim().replace(/^\/+|\/+$/g, "")}` : "/");
      if (passwordStrength) {
        const length = password?.value.length || 0;
        passwordStrength.textContent = length >= 12 ? "Senha forte." : length >= 8 ? "Senha fraca; recomendamos 12 ou mais caracteres." : "Mínimo de 8 caracteres.";
        passwordStrength.classList.toggle("is-weak", length >= 8 && length < 12);
        passwordStrength.classList.toggle("is-strong", length >= 12);
      }
    };
    const validateStep = () => {
      const current = panels.find((panel) => Number(panel.dataset.ftpStep) === step);
      const fields = [...(current?.querySelectorAll("input, select, textarea") || [])].filter((field) => !field.disabled && !field.closest("[hidden]"));
      if (step === 2 && confirmation && password) {
        confirmation.setCustomValidity(confirmation.value === password.value ? "" : "As senhas precisam ser iguais.");
      }
      const invalid = fields.find((field) => !field.checkValidity());
      if (invalid) { invalid.reportValidity(); invalid.focus(); return false; }
      return true;
    };
    const renderStep = () => {
      panels.forEach((panel) => { panel.hidden = Number(panel.dataset.ftpStep) !== step; });
      indicators.forEach((indicator) => {
        const number = Number(indicator.dataset.ftpStepIndicator);
        indicator.classList.toggle("is-active", number === step);
        indicator.classList.toggle("is-complete", number < step);
      });
      if (previous) previous.hidden = step === 1;
      if (next) next.hidden = step === panels.length;
      if (submit) submit.hidden = step !== panels.length;
      if (step === panels.length) {
        const review = ftpWizard.querySelector("[data-ftp-final-review]");
        if (review) review.innerHTML = [...ftpWizard.querySelectorAll("[data-ftp-summary]")].map((item) => `<dt>${item.previousElementSibling?.textContent || ""}</dt><dd>${item.textContent}</dd>`).join("");
      }
      panels.find((panel) => !panel.hidden)?.querySelector("input, select, textarea")?.focus({preventScroll: true});
      updateSummary();
    };
    next?.addEventListener("click", () => { if (validateStep()) { step += 1; renderStep(); } });
    previous?.addEventListener("click", () => { step = Math.max(1, step - 1); renderStep(); });
    form?.addEventListener("input", updateSummary);
    form?.addEventListener("change", updateSummary);
    form?.addEventListener("submit", (event) => {
      if (confirmation && password) confirmation.setCustomValidity(confirmation.value === password.value ? "" : "As senhas precisam ser iguais.");
      if (!form.checkValidity()) { event.preventDefault(); form.reportValidity(); }
    });
    ftpWizard.querySelector("[data-ftp-generate]")?.addEventListener("click", () => {
      const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_.!@#%";
      let generated = "";
      for (let index = 0; index < 14; index += 1) generated += alphabet[Math.floor(Math.random() * alphabet.length)];
      if (password && confirmation) { password.value = generated; confirmation.value = generated; confirmation.setCustomValidity(""); updateSummary(); }
    });
    ftpWizard.querySelector("[data-ftp-password-toggle]")?.addEventListener("click", (event) => {
      const show = password?.type === "password";
      if (password && confirmation) { password.type = show ? "text" : "password"; confirmation.type = password.type; }
      event.currentTarget.textContent = show ? "Ocultar senhas" : "Mostrar senhas";
    });
    updatePurpose();
    renderStep();
  }

  const equipmentWizard = document.querySelector("[data-equipment-wizard]");
  if (equipmentWizard) {
    const form = equipmentWizard.querySelector(".equipment-wizard-form");
    const panels = [...equipmentWizard.querySelectorAll("[data-equipment-step]")];
    const indicators = [...equipmentWizard.querySelectorAll("[data-equipment-step-indicator]")];
    const previous = equipmentWizard.querySelector("[data-equipment-previous]");
    const next = equipmentWizard.querySelector("[data-equipment-next]");
    const submit = equipmentWizard.querySelector("[data-equipment-submit]");
    const mikrotikMode = equipmentWizard.querySelector("[data-mikrotik-backup-mode]");
    let step = 1;
    const field = (name) => form?.elements.namedItem(name);
    const label = (name) => field(name)?.selectedOptions?.[0]?.textContent?.trim() || field(name)?.value?.trim() || "-";
    const updateSummary = () => {
      const isMikrotik = field("ssh_backup_driver")?.value === "mikrotik_routeros";
      if (mikrotikMode) mikrotikMode.hidden = !isMikrotik;
      const values = {name: label("hostname"), ip: label("ip_address"), environment: label("environment_id"), vendor: label("vendor_id"), group: label("group_id"), method: label("ssh_backup_driver")};
      Object.entries(values).forEach(([key, value]) => {
        const target = equipmentWizard.querySelector(`[data-equipment-summary="${key}"]`);
        if (target) target.textContent = value;
      });
    };
    const validateStep = () => {
      const panel = panels.find((item) => Number(item.dataset.equipmentStep) === step);
      const sshPassword = field("ssh_password");
      const sshConfirmation = field("ssh_confirm_password");
      if (step === 3 && sshPassword && sshConfirmation) {
        sshPassword.setCustomValidity(sshPassword.value.length >= 5 ? "" : "Use pelo menos 5 caracteres; recomendamos 10 ou mais.");
        sshConfirmation.setCustomValidity(sshPassword.value === sshConfirmation.value ? "" : "As senhas SSH precisam ser iguais.");
      }
      const invalid = [...(panel?.querySelectorAll("input, select, textarea") || [])].find((item) => !item.disabled && !item.checkValidity());
      if (invalid) { invalid.reportValidity(); invalid.focus(); return false; }
      return true;
    };
    const renderStep = () => {
      panels.forEach((panel) => { panel.hidden = Number(panel.dataset.equipmentStep) !== step; });
      indicators.forEach((indicator) => {
        const number = Number(indicator.dataset.equipmentStepIndicator);
        indicator.classList.toggle("is-active", number === step);
        indicator.classList.toggle("is-complete", number < step);
      });
      if (previous) previous.hidden = step === 1;
      if (next) next.hidden = step === panels.length;
      if (submit) submit.hidden = step !== panels.length;
      if (step === panels.length) {
        const review = equipmentWizard.querySelector("[data-equipment-final-review]");
        const rows = [["Nome", label("hostname")], ["IP / Host", label("ip_address")], ["Ambiente", label("environment_id")], ["Fabricante", label("vendor_id")], ["Grupo", label("group_id")], ["Localidade", label("pop_id")], ["Método", label("ssh_backup_driver")], ["Porta", label("ssh_port")]];
        if (field("ssh_backup_driver")?.value === "mikrotik_routeros") {
          const modeLabel = field("mikrotik_backup_mode")?.value === "ssh" ? "Backup via SSH — agendado pelo Backup Manager" : "Backup por FTP Push — agendado pelo MikroTik";
          rows.splice(7, 0, ["Método do backup", modeLabel]);
        }
        if (review) review.innerHTML = rows.map(([title, value]) => `<dt>${title}</dt><dd>${value}</dd>`).join("");
      }
      updateSummary();
    };
    next?.addEventListener("click", () => { if (validateStep()) { step += 1; renderStep(); } });
    previous?.addEventListener("click", () => { step = Math.max(1, step - 1); renderStep(); });
    form?.addEventListener("input", updateSummary);
    form?.addEventListener("change", updateSummary);
    form?.addEventListener("submit", (event) => {
      const sshPassword = field("ssh_password");
      const sshConfirmation = field("ssh_confirm_password");
      if (sshPassword) {
        sshPassword.setCustomValidity(sshPassword.value.length >= 5 ? "" : "Use pelo menos 5 caracteres; recomendamos 10 ou mais.");
      }
      if (sshPassword && sshConfirmation) sshConfirmation.setCustomValidity(sshPassword.value === sshConfirmation.value ? "" : "As senhas SSH precisam ser iguais.");
      if (!form.checkValidity()) { event.preventDefault(); form.reportValidity(); }
    });
    renderStep();
  }

  const equipmentEdit = document.querySelector("[data-equipment-edit]");
  if (equipmentEdit) {
    const form = equipmentEdit.querySelector(".equipment-edit-form");
    const field = (name) => form?.elements.namedItem(name);
    const value = (name) => field(name)?.selectedOptions?.[0]?.textContent?.trim() || field(name)?.value?.trim() || "-";
    const update = () => {
      const summary = {name: value("hostname"), ip: value("ip_address"), environment: value("environment_id"), vendor: value("vendor_id"), method: value("ssh_backup_driver"), port: value("ssh_port")};
      Object.entries(summary).forEach(([key, text]) => {
        const target = equipmentEdit.querySelector(`[data-edit-summary="${key}"]`);
        if (target) target.textContent = text;
      });
    };
    form?.addEventListener("input", update);
    form?.addEventListener("change", update);
    update();
  }

  const equipmentTabLinks = [...document.querySelectorAll("[data-equipment-tab-link]")];
  const equipmentTabPanels = [...document.querySelectorAll("[data-equipment-tab-panel]")];
  if (equipmentTabLinks.length && equipmentTabPanels.length) {
    const tabStorageKey = `equipment-active-tab:${window.location.pathname}`;
    const validTabs = new Set(equipmentTabPanels.map((panel) => panel.dataset.equipmentTabPanel));
    const activateEquipmentTab = (requested, updateHash = true) => {
      const selected = validTabs.has(requested) ? requested : "resumo";
      equipmentTabPanels.forEach((panel) => { panel.hidden = panel.dataset.equipmentTabPanel !== selected; });
      equipmentTabLinks.forEach((link) => {
        const active = link.dataset.equipmentTabLink === selected;
        link.classList.toggle("is-active", active);
        link.setAttribute("aria-current", active ? "page" : "false");
      });
      if (updateHash && window.location.hash !== `#${selected}`) history.replaceState(null, "", `#${selected}`);
    };
    equipmentTabLinks.forEach((link) => link.addEventListener("click", (event) => {
      event.preventDefault();
      activateEquipmentTab(link.dataset.equipmentTabLink || "resumo");
      const targetId = (link.getAttribute("href") || "").replace(/^#/, "");
      if (targetId && targetId !== link.dataset.equipmentTabLink) {
        window.requestAnimationFrame(() => document.getElementById(targetId)?.scrollIntoView({behavior: "smooth", block: "start"}));
      }
    }));
    equipmentTabPanels.forEach((panel) => {
      panel.querySelectorAll("form").forEach((form) => form.addEventListener("submit", () => {
        sessionStorage.setItem(tabStorageKey, panel.dataset.equipmentTabPanel || "resumo");
      }));
    });
    document.querySelectorAll("[data-equipment-open-dialog]").forEach((button) => {
      button.addEventListener("click", () => {
        activateEquipmentTab(button.dataset.equipmentDialogTab || "diagnostico");
        document.getElementById(button.dataset.equipmentOpenDialog || "")?.showModal();
      });
    });
    const rememberedTab = sessionStorage.getItem(tabStorageKey) || "";
    sessionStorage.removeItem(tabStorageKey);
    const hashTarget = window.location.hash.slice(1);
    const nestedTarget = hashTarget ? document.getElementById(hashTarget) : null;
    const nestedPanel = nestedTarget?.closest("[data-equipment-tab-panel]");
    const initialTab = validTabs.has(hashTarget) ? hashTarget : nestedPanel?.dataset.equipmentTabPanel || rememberedTab;
    activateEquipmentTab(initialTab, false);
    if (nestedTarget && nestedPanel) window.requestAnimationFrame(() => nestedTarget.scrollIntoView({behavior: "smooth", block: "start"}));
  }

  const historyFilters = document.querySelector("[data-equipment-history-filters]");
  if (historyFilters) {
    const tableRows = [...document.querySelectorAll(".equipment-modern-table tbody tr")];
    const dateFrom = historyFilters.querySelector("[data-history-date-from]");
    const dateTo = historyFilters.querySelector("[data-history-date-to]");
    const periodLabel = historyFilters.querySelector("[data-history-period-label]");
    const defaultPeriodLabel = periodLabel?.textContent || "Todos os períodos";
    const rowDate = (value) => {
      const match = value.match(/^(\d{2})\/(\d{2})\/(\d{4})/);
      return match ? `${match[3]}-${match[2]}-${match[1]}` : "";
    };
    const refreshHistory = () => {
      const query = (historyFilters.querySelector("[data-history-search]")?.value || "").trim().toLocaleLowerCase("pt-BR");
      const method = (historyFilters.querySelector("[data-history-method]")?.value || "").toLocaleLowerCase("pt-BR");
      const status = (historyFilters.querySelector("[data-history-status]")?.value || "").toLocaleLowerCase("pt-BR");
      const from = dateFrom?.value || "";
      const to = dateTo?.value || "";
      tableRows.forEach((row) => {
        if (row.querySelector("td[colspan]")) return;
        const cells = [...row.cells].map((cell) => cell.textContent.trim().toLocaleLowerCase("pt-BR"));
        const date = rowDate(cells[0] || "");
        row.hidden = Boolean((query && !cells[1]?.includes(query)) || (method && !cells[2]?.includes(method)) || (status && !cells[3]?.includes(status)) || (from && date < from) || (to && date > to));
      });
    };
    historyFilters.addEventListener("input", refreshHistory);
    historyFilters.addEventListener("change", refreshHistory);
    historyFilters.querySelector("[data-history-date-apply]")?.addEventListener("click", () => {
      if (dateFrom?.value && dateTo?.value && dateFrom.value > dateTo.value) {
        dateTo.setCustomValidity("A data final deve ser igual ou posterior à data inicial.");
        dateTo.reportValidity();
        return;
      }
      dateTo?.setCustomValidity("");
      const formatDate = (value) => value ? value.split("-").reverse().join("/") : "";
      if (periodLabel) periodLabel.textContent = dateFrom?.value || dateTo?.value ? `${formatDate(dateFrom?.value) || "Início"} - ${formatDate(dateTo?.value) || "Hoje"}` : defaultPeriodLabel;
      refreshHistory();
      historyFilters.querySelector(".equipment-history-period")?.removeAttribute("open");
    });
    historyFilters.querySelector("[data-history-date-clear]")?.addEventListener("click", () => {
      if (dateFrom) dateFrom.value = "";
      if (dateTo) { dateTo.value = ""; dateTo.setCustomValidity(""); }
      if (periodLabel) periodLabel.textContent = defaultPeriodLabel;
      refreshHistory();
      historyFilters.querySelector(".equipment-history-period")?.removeAttribute("open");
    });
  }

  document.querySelectorAll("[data-toggle-details]").forEach((button) => {
    const details = document.getElementById(button.dataset.toggleDetails || "");
    if (!(details instanceof HTMLDetailsElement)) return;
    const sync = () => button.setAttribute("aria-expanded", String(details.open));
    button.addEventListener("click", () => { details.open = !details.open; sync(); if (details.open) details.querySelector("input, select")?.focus(); });
    details.addEventListener("toggle", sync);
    sync();
  });

  document.querySelectorAll("[data-job-frequency-form]").forEach((form) => {
    const frequency = form.querySelector("[data-job-frequency]");
    const weekdays = form.querySelector("[data-job-weekdays]");
    const timeField = form.querySelector("[data-job-time-field]");
    const checks = [...form.querySelectorAll('[name="schedule_days"]')];
    const help = form.querySelector("[data-job-weekdays-help]");
    if (!frequency || !weekdays) return;
    const syncFrequency = () => {
      const daily = frequency.value === "daily";
      const weekly = frequency.value === "weekly";
      weekdays.hidden = !daily && !weekly;
      if (timeField) timeField.hidden = !daily && !weekly;
      weekdays.classList.toggle("is-daily", daily);
      checks.forEach((check) => {
        check.disabled = daily;
      });
      if (help) help.textContent = daily ? "O backup será executado todos os dias." : "Escolha um ou mais dias da semana para executar o backup.";
    };
    frequency.addEventListener("change", syncFrequency);
    syncFrequency();
  });

  document.querySelectorAll("[data-job-time-picker]").forEach((picker) => {
    const popover = picker.querySelector("[data-job-time-popover]");
    const value = picker.querySelector("[data-job-time-value]");
    const label = picker.querySelector("[data-job-time-label]");
    const hour = picker.querySelector("[data-job-hour]");
    const minute = picker.querySelector("[data-job-minute]");
    picker.querySelector("[data-job-time-open]")?.addEventListener("click", () => { if (popover) popover.hidden = !popover.hidden; });
    picker.querySelector("[data-job-time-apply]")?.addEventListener("click", () => {
      const selected = `${hour?.value || "00"}:${minute?.value || "00"}`;
      if (value) value.value = selected;
      if (label) label.textContent = selected;
      if (popover) popover.hidden = true;
    });
  });

  document.querySelectorAll("[data-summary-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      const details = document.getElementById("telegram-config");
      if (!(details instanceof HTMLDetailsElement)) return;
      details.open = true;
      const panel = details.querySelector(`[data-summary-panel="${CSS.escape(button.dataset.summaryEdit || "")}"]`);
      panel?.scrollIntoView({behavior: "smooth", block: "center"});
      panel?.querySelector("input, select")?.focus();
    });
  });

  const historyPanel = document.querySelector(".notification-history");
  if (historyPanel) {
    let historyFilter = "all";
    const refreshHistory = () => {
      historyPanel.querySelectorAll("[data-history-group]").forEach((group) => {
        const expanded = group.dataset.expanded === "true";
        const matching = [...group.querySelectorAll("[data-history-item]")].filter((item) => historyFilter === "all" || item.dataset.historyStatus === historyFilter);
        group.querySelectorAll("[data-history-item]").forEach((item) => { item.hidden = true; });
        matching.forEach((item, index) => { item.hidden = !expanded && index >= 8; });
        const more = group.querySelector("[data-history-more]");
        if (more) {
          more.hidden = matching.length <= 8;
          more.textContent = expanded ? "Mostrar menos" : `Mostrar mais (${matching.length - 8})`;
        }
      });
    };
    historyPanel.querySelectorAll("[data-history-filter]").forEach((button) => button.addEventListener("click", () => {
      historyFilter = button.dataset.historyFilter || "all";
      historyPanel.querySelectorAll("[data-history-filter]").forEach((item) => item.classList.toggle("active", item === button));
      refreshHistory();
    }));
    historyPanel.querySelectorAll("[data-history-more]").forEach((button) => button.addEventListener("click", () => {
      const group = button.closest("[data-history-group]");
      if (group) group.dataset.expanded = String(group.dataset.expanded !== "true");
      refreshHistory();
    }));
    refreshHistory();
  }

  document.querySelectorAll("[data-maintenance-toggle]").forEach((toggle) => {
    const update = () => {
      const times = toggle.closest(".maintenance-toggle")?.querySelector("[data-maintenance-times]");
      const state = toggle.closest(".notification-maintenance, .maintenance-toggle")?.querySelector("[data-maintenance-state]");
      if (times) times.hidden = !toggle.checked;
      if (state) state.textContent = toggle.checked ? "Ativada" : "Desativada";
    };
    toggle.addEventListener("change", update);
    update();
  });

  document.querySelectorAll("[data-preview-tab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      const dialog = tab.closest("dialog");
      dialog?.querySelectorAll("[data-preview-tab]").forEach((item) => item.setAttribute("aria-selected", String(item === tab)));
      dialog?.querySelectorAll("[data-preview-panel]").forEach((panel) => { panel.hidden = panel.dataset.previewPanel !== tab.dataset.previewTab; });
    });
  });
  document.querySelectorAll("details[data-operation-result]").forEach((details) => {
    details.addEventListener("toggle", () => {
      const label = details.querySelector("[data-details-label]");
      if (label) label.textContent = details.open ? "Ocultar detalhes" : "Ver detalhes";
    });
  });
  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const source = document.querySelector(`[data-copy-source="${CSS.escape(button.dataset.copyTarget || "")}"]`);
      if (!source) return;
      try {
        await navigator.clipboard.writeText(source.textContent || "");
        const original = button.textContent;
        button.textContent = "Copiado";
        window.setTimeout(() => { button.textContent = original; }, 1800);
      } catch (_) {
        const range = document.createRange();
        range.selectNodeContents(source);
        window.getSelection()?.removeAllRanges();
        window.getSelection()?.addRange(range);
      }
    });
  });

  document.querySelectorAll("[data-mikrotik-schedule]").forEach((select) => {
    const timeLabel = select.form?.querySelector("[data-mikrotik-schedule-time]");
    const timeInput = timeLabel?.querySelector('input[name="schedule_time"]');
    const update = () => {
      const daily = select.value === "scheduled";
      if (timeLabel) timeLabel.hidden = !daily;
      if (timeInput) timeInput.required = daily;
    };
    select.addEventListener("change", update);
    update();
  });

  const finalStates = new Set(["success", "failed", "timeout", "cancelled", "skipped"]);
  document.querySelectorAll(".notice[data-auto-dismiss]").forEach((notice) => {
    const delay = Number(notice.dataset.autoDismiss || "6000");
    let remaining = delay;
    let started = Date.now();
    let timer = 0;
    const clear = () => window.clearTimeout(timer);
    const resume = () => {
      clear();
      started = Date.now();
      timer = window.setTimeout(() => notice.remove(), remaining);
    };
    const pause = () => {
      clear();
      remaining = Math.max(0, remaining - (Date.now() - started));
    };
    notice.addEventListener("mouseenter", pause);
    notice.addEventListener("mouseleave", resume);
    notice.addEventListener("focusin", pause);
    notice.addEventListener("focusout", resume);
    resume();
  });

  const loadingMessages = {
    "/ssh-test": "Testando conexão...",
    "/jobs/run-now": "Gerando backup...",
    "/backups/import": "Importando...",
    "/lifecycle/execute": "Limpando...",
    "/restore": "Restaurando...",
    "/settings/notifications/test": "Enviando notificação..."
  };
  document.querySelectorAll('form[method="post"]').forEach((form) => {
    form.addEventListener("submit", () => {
      if (form.matches("[data-notification-test]")) return;
      const button = form.querySelector('button[type="submit"], button:not([type])');
      if (!button || button.disabled) return;
      const action = form.getAttribute("action") || "";
      const matched = Object.entries(loadingMessages).find(([suffix]) => action.endsWith(suffix));
      if (!matched) return;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      button.textContent = matched[1];
    });
  });

  const mikrotikTerminalStates = new Set(["installed", "installed_valid", "repaired", "needs_repair", "validated", "failed", "expired", "cancelled"]);
  let operationNotice;
  let operationNoticeTimer;
  const showOperationNotice = (state, message, busy = false) => {
    window.clearTimeout(operationNoticeTimer);
    const notice = operationNotice?.isConnected ? operationNotice : document.createElement("div");
    operationNotice = notice;
    notice.className = `notice operation-toast ${state === "success" ? "success" : state === "error" ? "error" : "info"}`;
    notice.setAttribute("role", state === "error" ? "alert" : "status");
    notice.setAttribute("aria-live", state === "error" ? "assertive" : "polite");
    const text = document.createElement("span");
    text.textContent = message;
    const close = document.createElement("button");
    close.className = "notice-close";
    close.type = "button";
    close.setAttribute("aria-label", "Fechar mensagem");
    close.textContent = "×";
    close.addEventListener("click", () => { notice.remove(); operationNotice = undefined; });
    notice.replaceChildren(...(busy ? [Object.assign(document.createElement("span"), {className: "operation-spinner"})] : []), text, close);
    if (!notice.isConnected) document.body.append(notice);
    if (!busy) operationNoticeTimer = window.setTimeout(() => notice.remove(), state === "error" ? 12000 : 6500);
  };
  const pollNotificationTest = (attempt = 0) => {
    window.setTimeout(async () => {
      try {
        const response = await fetch("/settings/notifications/status", {headers: {"Accept": "application/json"}});
        if (!response.ok) throw new Error("Não foi possível acompanhar o teste.");
        const data = await response.json();
        if (data.terminal) {
          showOperationNotice(data.status === "sent" ? "success" : "error", data.message || "Teste concluído.");
        } else if (attempt < 20) {
          showOperationNotice("info", data.message || "Mensagem adicionada à fila.", true);
          pollNotificationTest(attempt + 1);
        } else {
          showOperationNotice("info", "O teste continua na fila. Consulte o histórico recente.");
        }
      } catch (error) {
        showOperationNotice("error", error instanceof Error ? error.message : "Não foi possível acompanhar o teste.");
      }
    }, 3000);
  };
  document.querySelectorAll("form[data-notification-test]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector("button[type='submit']");
      if (!button || button.disabled) return;
      const original = button.textContent;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      showOperationNotice("info", "Mensagem adicionada à fila. O envio pode levar até 1 minuto.", true);
      try {
        const response = await fetch(form.action, {method: "POST", body: new URLSearchParams(new FormData(form)), credentials: "same-origin", headers: {"Accept": "text/html"}});
        if (!response.ok) {
          if (response.status === 403) {
            throw new Error("Sessão expirada ou formulário desatualizado. Atualize a página e tente novamente.");
          }
          throw new Error("Não foi possível adicionar a mensagem à fila.");
        }
        if (form.action.endsWith("/test")) pollNotificationTest();
        else showOperationNotice("success", "Resumo de teste adicionado à fila. O envio pode levar até 1 minuto.");
      } catch (error) {
        showOperationNotice("error", error instanceof Error ? error.message : "Não foi possível enviar.");
      } finally {
        button.disabled = false;
        button.removeAttribute("aria-busy");
        button.textContent = original;
      }
    });
  });
  const operationError = (message, code = "communication_error") => ({
    ok: false, status: "failed", message, error_code: code, terminal: true,
    timeline: [{step: "Comunicação com o servidor", status: "failed"}]
  });
  const installWizard = document.getElementById("mikrotik-install-wizard");
  const ftpTestDialog = document.getElementById("mikrotik-ftp-test-progress");
  let installProgressTimer;
  let installCloseTimer;
  let ftpCloseTimer;
  const updateInstallWizard = (percent, stage, error = "") => {
    const progress = installWizard?.querySelector("[data-install-progress]");
    const percentLabel = installWizard?.querySelector("[data-install-percent]");
    const stageLabel = installWizard?.querySelector("[data-install-stage]");
    const errorBox = installWizard?.querySelector("[data-install-error]");
    const progressBox = installWizard?.querySelector(".wizard-progress");
    if (progress) progress.value = percent;
    if (percentLabel) percentLabel.textContent = `${percent}%`;
    if (stageLabel) stageLabel.textContent = stage;
    if (progressBox) progressBox.dataset.state = error ? "failed" : (percent >= 100 ? "completed" : "running");
    if (errorBox) { errorBox.textContent = error; errorBox.hidden = !error; }
  };
  const setInstallWizardComplete = (complete) => {
    const submit = installWizard?.querySelector('[data-install-wizard-form] button[type="submit"]');
    const close = installWizard?.querySelector("[data-dialog-close]");
    if (submit) { submit.hidden = complete; submit.style.display = complete ? "none" : ""; }
    if (close) close.textContent = complete ? "Fechar" : "Cancelar";
  };
  installWizard?.addEventListener("close", () => {
    window.clearInterval(installProgressTimer);
    window.clearTimeout(installCloseTimer);
    updateInstallWizard(0, "Pronto para iniciar");
    setInstallWizardComplete(false);
  });
  const mikrotikStatusLabel = (status) => ({
    done: "Concluído", completed: "Concluído", validated: "Validado",
    installed_valid: "Instalação válida", installed: "Instalado", repaired: "Corrigido",
    needs_repair: "Precisa de atualização", pending: "Pendente", running: "Em andamento",
    waiting_upload: "Aguardando arquivo", validating: "Validando", failed: "Falhou",
    expired: "Expirado", cancelled: "Cancelado", not_executed: "Não executado",
    unknown: "Desconhecido", not_tested: "Não testado", ready: "Pronto"
  }[status] || String(status || "unknown").replaceAll("_", " "));
  const renderMikrotikOperation = (kind, data) => {
    const key = kind === "ftp" ? "ftp" : "installation";
    const card = document.querySelector(`[data-operation-result="${key}"]`);
    if (!card) throw new Error("Componente de resultado da operação não encontrado.");
    card.hidden = false;
    const details = card instanceof HTMLDetailsElement ? card : null;
    if (details) {
      if (data.ok) {
        window.setTimeout(() => {
          if (details.open && !details.matches(":hover") && !details.contains(document.activeElement)) {
            details.open = false;
          }
        }, 7000);
      } else {
        details.open = Boolean(data.terminal);
      }
    }
    const status = card.querySelector("[data-operation-status]");
    const message = card.querySelector("[data-operation-message]");
    const timeline = card.querySelector("[data-operation-timeline]");
    if (!status || !message || !timeline) throw new Error("Componente de resultado incompleto.");
    card.dataset.state = data.status || "unknown";
    status.textContent = mikrotikStatusLabel(data.status);
    status.className = `badge status-${data.status || "unknown"}`;
    message.textContent = data.message || "A operação terminou sem uma mensagem válida.";
    timeline.replaceChildren(...(Array.isArray(data.timeline) ? data.timeline : []).map((item) => {
      const row = document.createElement("li");
      row.className = `operation-step status-${item.status || "pending"}`;
      const label = document.createElement("span");
      label.textContent = item.step || "Etapa";
      const state = document.createElement("strong");
      state.textContent = mikrotikStatusLabel(item.status || "pending");
      row.append(label, state);
      return row;
    }));
    const steps = Array.isArray(data.timeline) ? data.timeline : [];
    const reached = steps.filter((item) => ["done", "completed", "validated", "failed"].includes(item.status)).length;
    const operationPercent = steps.length ? Math.round((reached / steps.length) * 100) : 0;
    const operationProgress = card.querySelector("[data-operation-progress]");
    const operationPercentLabel = card.querySelector("[data-operation-percent]");
    if (operationProgress) operationProgress.value = operationPercent;
    if (operationPercentLabel) operationPercentLabel.textContent = `${operationPercent}%`;
    const time = card.querySelector("[data-operation-time]");
    if (time) time.textContent = new Date().toLocaleString("pt-BR");
    if (key === "ftp") {
      if (data.status === "validated") {
        status.textContent = "Aprovado";
        message.textContent = "Teste FTP aprovado com sucesso.";
        window.clearTimeout(ftpCloseTimer);
        ftpCloseTimer = window.setTimeout(() => { if (ftpTestDialog?.open) ftpTestDialog.close(); }, 3000);
      }
      const ftpStatus = document.querySelector("[data-ftp-status]");
      if (ftpStatus) ftpStatus.textContent = ({validated: "Validado", expired: "Expirado", failed: "Falhou"}[data.status] || "Testando");
      const summary = document.querySelector("[data-ftp-summary]");
      if (summary) summary.textContent = data.message;
      const recommendation = card.querySelector("[data-operation-recommendation]");
      if (recommendation) recommendation.textContent = data.action_recommendation || "";
      card.dataset.terminal = String(Boolean(data.terminal));
      const countdown = card.querySelector("[data-operation-countdown]");
      if (countdown) {
        const remaining = data.expires_at ? Math.max(0, Math.ceil((new Date(`${data.expires_at.replace(" ", "T")}Z`) - Date.now()) / 1000)) : 0;
        countdown.textContent = data.status === "waiting_upload" ? `Aguardando arquivo no servidor — aproximadamente ${remaining} segundos restantes.` : "";
      }
      const fields = {"[data-operation-started]": data.started_at, "[data-operation-expires]": data.expires_at,
        "[data-operation-checked]": data.last_check_at, "[data-operation-filename]": data.expected_filename};
      Object.entries(fields).forEach(([selector, value]) => { const element = card.querySelector(selector); if (element && value) element.textContent = value; });
      const retry = card.querySelector("[data-retry-ftp]");
      if (retry) {
        const running = ["pending", "running", "waiting_upload", "validating"].includes(data.status);
        const retryable = ["failed", "expired"].includes(data.status);
        retry.hidden = !(running || retryable);
        retry.disabled = running;
        retry.setAttribute("aria-busy", String(running));
        retry.classList.toggle("is-running", running);
        retry.textContent = running ? "Teste em execução…" : "Tentar novamente";
      }
    } else {
      const installation = document.querySelector("[data-installation-summary]");
      if (installation) installation.textContent = data.status === "needs_repair" ? "Precisa de reparo" :
        (data.ok ? "Instalado e atualizado" : "Estado desconhecido");
      if (data.ok) document.querySelector('form[data-mikrotik-operation="ftp"] button')?.removeAttribute("disabled");
      if (installWizard) {
        window.clearInterval(installProgressTimer);
        const current = Number(installWizard.querySelector("[data-install-progress]")?.value || 10);
        const needsRepair = data.status === "needs_repair";
        const timeline = Array.isArray(data.timeline) ? data.timeline : [];
        const reached = timeline.length ? Math.round((timeline.filter((item) => ["done", "failed"].includes(item.status)).length / timeline.length) * 100) : 0;
        const failureTitle = ({
          SSH_CONNECTION_REFUSED: "Falha na conexão SSH",
          SSH_CONNECTION_FAILED: "Não foi possível conectar por SSH",
          SSH_TIMEOUT: "Tempo esgotado na conexão SSH",
          SSH_AUTH_FAILED: "Usuário ou senha SSH recusados",
          SSH_CREDENTIAL_MISSING: "Credencial SSH não configurada",
          TELNET_CONNECTION_FAILED: "Não foi possível conectar por Telnet",
          ROUTEROS_VERSION_UNSUPPORTED: "Versão do RouterOS incompatível",
          FTP_AUTH_FAILED: "Usuário ou senha FTP recusados"
        })[data.error_code] || "Não foi possível concluir";
        updateInstallWizard(data.ok ? 100 : Math.max(10, current, reached), data.ok ? "Operação concluída" : (needsRepair ? "Atualização disponível" : failureTitle), data.ok || needsRepair ? "" : `Possível causa e correção: ${data.message}`);
        setInstallWizardComplete(Boolean(data.ok && !needsRepair));
        const close = installWizard.querySelector("[data-dialog-close]");
        if (close && !data.ok && !needsRepair) close.textContent = "Fechar";
      }
    }
  };
  const readOperationResponse = async (response) => {
    const contentType = response.headers.get("Content-Type") || "";
    if (response.redirected || !contentType.toLowerCase().includes("application/json")) {
      throw new Error("O servidor retornou uma resposta inesperada. Atualize a página e tente novamente.");
    }
    let data;
    try { data = await response.json(); }
    catch (_) { throw new Error("O servidor retornou JSON inválido. Atualize a página e tente novamente."); }
    if (!data || typeof data.status !== "string" || typeof data.message !== "string") {
      throw new Error("A resposta do servidor não contém status e mensagem.");
    }
    if (!response.ok && data.ok !== false) throw new Error(data.message || `Erro HTTP ${response.status}.`);
    return data;
  };
  const fetchOperation = async (url, options = {}, timeoutMs = 12000) => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, {...options, signal: controller.signal});
    } catch (error) {
      if (error?.name === "AbortError") throw new Error("O servidor demorou demais para responder. A operação foi encerrada com segurança.");
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  };
  const pollMikrotikFtp = (card, url, attempt = 0) => {
    if (!url) return;
    const poll = async () => {
      try {
        if (attempt >= 320) throw new Error("O teste excedeu o prazo máximo de 16 minutos. Consulte o diagnóstico e tente novamente.");
        const response = await fetchOperation(url, {headers: {"Accept": "application/json"}});
        const data = await readOperationResponse(response);
        renderMikrotikOperation("ftp", data);
        if (data.terminal || mikrotikTerminalStates.has(data.status)) {
          showOperationNotice(data.ok ? "success" : "error", data.message);
          const button = document.querySelector('form[data-mikrotik-operation="ftp"] button');
          if (button) {
            button.removeAttribute("disabled");
            button.removeAttribute("aria-busy");
            button.classList.remove("is-running");
            button.textContent = button.dataset.originalText || "Testar envio FTP";
          }
          return;
        }
        showOperationNotice("info", data.status === "waiting_upload" ? "Aguardando arquivo no servidor…" : data.message, true);
        window.setTimeout(() => pollMikrotikFtp(card, url, attempt + 1), 3000);
      } catch (error) {
        const data = operationError(error instanceof Error ? error.message : "Erro de comunicação durante o polling.");
        renderMikrotikOperation("ftp", data);
        showOperationNotice("error", data.message);
        const button = document.querySelector('form[data-mikrotik-operation="ftp"] button');
        if (button) {
          button.removeAttribute("disabled");
          button.removeAttribute("aria-busy");
          button.classList.remove("is-running");
          button.textContent = button.dataset.originalText || "Testar envio FTP";
        }
      }
    };
    window.setTimeout(poll, 1500);
  };
  const pollManagedBackupReceipt = (url, button, attempt = 0) => {
    const updateReceivedSummary = (data) => {
      const panel = document.querySelector("[data-mikrotik-received-summary]");
      if (!panel || !data.latest_upload_filename) return;
      const size = Number(data.latest_upload_size || 0);
      const units = ["B", "KB", "MB", "GB"];
      let amount = size;
      let unit = 0;
      while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
      const formattedSize = unit === 0 ? `${amount} B` : `${amount.toFixed(1).replace(".0", "")} ${units[unit]}`;
      const receivedAt = data.latest_upload_received_at
        ? new Date(`${String(data.latest_upload_received_at).replace(" ", "T")}Z`).toLocaleString("pt-BR") : "Agora";
      const values = {
        "[data-received-filename]": data.latest_upload_filename,
        "[data-received-type]": String(data.latest_upload_type || "-").toUpperCase(),
        "[data-received-size]": formattedSize,
        "[data-received-at]": receivedAt,
      };
      Object.entries(values).forEach(([selector, value]) => {
        const element = panel.querySelector(selector);
        if (element) element.textContent = value;
      });
    };
    const poll = async () => {
      try {
        if (attempt >= 100) throw new Error("O servidor FTP não confirmou os arquivos dentro de 5 minutos.");
        const response = await fetchOperation(url, {headers: {"Accept": "application/json"}});
        const data = await readOperationResponse(response);
        if (data.terminal) {
          if (data.ok) {
            updateReceivedSummary(data);
            renderMikrotikOperation("installation", {...data, status: "installed", timeline: [
              {step: "Backup executado no MikroTik", status: "done"},
              {step: "Arquivos recebidos e validados no servidor", status: "done"}
            ]});
            updateInstallWizard(100, "Backup recebido e validado com sucesso");
            showOperationNotice("success", data.message);
            installCloseTimer = window.setTimeout(() => { if (installWizard?.open) installWizard.close(); }, 3000);
          } else {
            updateInstallWizard(Math.max(92, Number(installWizard?.querySelector("[data-install-progress]")?.value || 92)), "Falha ao validar o recebimento FTP", data.message);
            showOperationNotice("error", data.message);
          }
          button.disabled = false;
          button.removeAttribute("aria-busy");
          button.textContent = button.dataset.originalText || "Iniciar instalação";
          return;
        }
        const percent = Math.min(98, 92 + Math.floor(attempt / 3));
        updateInstallWizard(percent, "Aguardando confirmação do servidor FTP…");
        window.setTimeout(() => pollManagedBackupReceipt(url, button, attempt + 1), 3000);
      } catch (error) {
        const message = error instanceof Error ? error.message : "Não foi possível confirmar o recebimento FTP.";
        updateInstallWizard(Math.max(92, Number(installWizard?.querySelector("[data-install-progress]")?.value || 92)), "Falha ao confirmar o recebimento FTP", message);
        showOperationNotice("error", message);
        button.disabled = false;
        button.removeAttribute("aria-busy");
        button.textContent = button.dataset.originalText || "Iniciar instalação";
      }
    };
    window.setTimeout(poll, 1500);
  };
  document.querySelectorAll("form[data-mikrotik-operation]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const kind = form.dataset.mikrotikOperation || "installation";
      const scriptVersion = form.dataset.scriptVersion || "atual";
      const button = form.querySelector('button[type="submit"], button:not([type])');
      if (!button || button.disabled) return;
      const original = button.textContent;
      let keepDisabled = false;
      let repairAvailable = false;
      button.dataset.originalText = original;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      if (kind === "ftp") button.classList.add("is-running");
      button.textContent = kind === "ftp" ? "Teste em execução…" : (kind === "repair" ? "Reparando..." : "Instalando...");
      if (kind === "ftp") {
        window.clearTimeout(ftpCloseTimer);
        if (ftpTestDialog && !ftpTestDialog.open) ftpTestDialog.showModal();
        renderMikrotikOperation("ftp", {ok: true, status: "running", message: "Preparando o teste FTP…", terminal: false, timeline: [
          {step: "Teste iniciado", status: "running"}, {step: "Arquivo criado no MikroTik", status: "pending"},
          {step: "Comando FTP executado", status: "pending"}, {step: "Arquivo recebido no servidor", status: "pending"},
          {step: "Arquivo validado", status: "pending"}, {step: "Limpeza concluída", status: "pending"},
          {step: "Teste concluído", status: "pending"}
        ]});
      }
      if (kind === "install" && form.matches("[data-install-wizard-form]")) {
        let percent = 8;
        window.clearTimeout(installCloseTimer);
        setInstallWizardComplete(false);
        updateInstallWizard(percent, "Conectando ao MikroTik por SSH…");
        window.clearInterval(installProgressTimer);
        installProgressTimer = window.setInterval(() => {
          percent = Math.min(82, percent + 6);
          const stage = percent < 25 ? "Detectando a versão do RouterOS…" : percent < 45 ? "Verificando a instalação atual…" : percent < 70 ? "Instalando script e scheduler…" : "Validando a instalação…";
          updateInstallWizard(percent, stage);
        }, 900);
      }
      showOperationNotice("info", kind === "ftp" ? "Preparando teste FTP…" : "Conectando via SSH…", true);
      try {
        const response = await fetchOperation(form.action, {method: "POST", headers: {"Accept": "application/json"}}, 90000);
        const data = await readOperationResponse(response);
        renderMikrotikOperation(kind, data);
        repairAvailable = kind === "install" && data.status === "needs_repair";
        showOperationNotice(repairAvailable || data.terminal === false ? "info" : (data.ok ? "success" : "error"), data.message, repairAvailable || data.terminal === false);
        if (repairAvailable) {
          form.action = form.action.replace(/\/install-ssh$/, "/repair-ssh");
          updateInstallWizard(Math.max(50, Number(installWizard?.querySelector("[data-install-progress]")?.value || 50)), `Atualização v${scriptVersion} disponível`);
        }
        if (kind === "ftp" && !data.terminal && data.operation_uuid) {
          const card = document.querySelector('[data-operation-result="ftp"]');
          const url = `/mikrotik-ftp/tests/${encodeURIComponent(data.operation_uuid)}`;
          if (card) { card.dataset.statusUrl = url; keepDisabled = true; pollMikrotikFtp(card, url); }
        }
        if (kind === "install" && data.ok && installWizard?.querySelector("[data-install-run-after]")?.checked) {
          updateInstallWizard(88, "Executando backup completo no MikroTik…");
          const executeUrl = form.action.replace(/\/(?:install|repair)-ssh$/, "/execute-ssh");
          const executeResponse = await fetchOperation(executeUrl, {method: "POST", headers: {"Accept": "application/json"}}, 180000);
          const executeData = await readOperationResponse(executeResponse);
          if (executeData.ok && !executeData.terminal && executeData.status_url) {
            keepDisabled = true;
            updateInstallWizard(92, "Aguardando confirmação do servidor FTP…");
            showOperationNotice("info", executeData.message, true);
            pollManagedBackupReceipt(executeData.status_url, button);
          } else if (executeData.ok) {
            updateInstallWizard(100, "Backup recebido e validado com sucesso");
            showOperationNotice("success", executeData.message);
          } else {
            updateInstallWizard(Number(installWizard?.querySelector("[data-install-progress]")?.value || 88), "Falha ao executar o backup completo", executeData.message);
            showOperationNotice("error", executeData.message);
          }
          if (executeData.ok && executeData.terminal) installCloseTimer = window.setTimeout(() => { if (installWizard.open) installWizard.close(); }, 3000);
        } else if (kind === "install" && data.ok) {
          installCloseTimer = window.setTimeout(() => { if (installWizard?.open) installWizard.close(); }, 3000);
        }
      } catch (error) {
        const data = operationError(error instanceof Error ? error.message : "Erro de comunicação com o servidor.");
        renderMikrotikOperation(kind, data);
        showOperationNotice("error", data.message);
        if (kind === "install") {
          window.clearInterval(installProgressTimer);
          updateInstallWizard(Number(installWizard?.querySelector("[data-install-progress]")?.value || 10), "Não foi possível concluir", data.message);
        }
      } finally {
        if (!keepDisabled) button.disabled = false;
        button.removeAttribute("aria-busy");
        if (!keepDisabled) button.classList.remove("is-running");
        if (!keepDisabled) button.textContent = repairAvailable ? `Aplicar atualização v${scriptVersion}` : original;
      }
    });
  });
  document.querySelectorAll('[data-operation-result="ftp"][data-status-url]').forEach((card) => {
    if (card.dataset.terminal !== "true") pollMikrotikFtp(card, card.dataset.statusUrl || "");
  });

  const manualDialog = document.getElementById("mikrotik-manual-script");
  let manualTrigger;
  const clearManualScript = () => {
    const source = manualDialog?.querySelector("[data-manual-script-content]");
    if (source) source.textContent = "";
    manualDialog?.querySelector("[data-manual-copy]")?.setAttribute("disabled", "");
  };
  document.querySelectorAll("[data-manual-script-form]").forEach((form) => form.addEventListener("submit", async (event) => {
    if (!manualDialog?.showModal) return;
    event.preventDefault();
    manualTrigger = event.submitter;
    manualDialog.showModal();
    const feedback = manualDialog.querySelector("[data-copy-feedback]");
    if (feedback) feedback.textContent = "Carregando script…";
    try {
      const response = await fetch(form.action, {method: "POST", headers: {"Accept": "text/plain"}});
      if (!response.ok || response.redirected || !(response.headers.get("Content-Type") || "").toLowerCase().includes("text/plain")) {
        throw new Error("O servidor retornou uma resposta inesperada ao carregar o script.");
      }
      const script = await response.text();
      const source = manualDialog.querySelector("[data-manual-script-content]");
      if (!source || !script) throw new Error("O servidor não retornou o script completo.");
      source.textContent = script;
      manualDialog.querySelector("[data-manual-copy]")?.removeAttribute("disabled");
      if (feedback) feedback.textContent = "";
    } catch (error) {
      if (feedback) feedback.textContent = error instanceof Error ? error.message : "Não foi possível carregar o script.";
    }
  }));
  manualDialog?.querySelectorAll("[data-manual-close]").forEach((button) => button.addEventListener("click", () => manualDialog.close()));
  manualDialog?.addEventListener("close", () => { clearManualScript(); manualTrigger?.focus(); });
  manualDialog?.addEventListener("click", (event) => { if (event.target === manualDialog) manualDialog.close(); });
  manualDialog?.querySelector("[data-manual-copy]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const source = manualDialog.querySelector("[data-manual-script-content]");
    const feedback = manualDialog.querySelector("[data-copy-feedback]");
    try {
      await navigator.clipboard.writeText(source?.textContent || "");
      button.textContent = "Copiado";
      if (feedback) feedback.textContent = "Script copiado para a área de transferência.";
      showOperationNotice("success", "Script copiado para a área de transferência.");
      window.setTimeout(() => { button.textContent = "Copiar script"; }, 1800);
    } catch (_) {
      if (feedback) feedback.textContent = "Não foi possível copiar o script. Selecione o conteúdo e copie manualmente.";
      showOperationNotice("error", "Não foi possível copiar o script.");
    }
  });
  document.querySelectorAll("[data-ssh-run-panel]").forEach((panel) => {
    const url = panel.dataset.statusUrl;
    const statusEl = panel.querySelector("[data-run-status]");
    const triggerEl = panel.querySelector("[data-run-trigger]");
    const methodEl = panel.querySelector("[data-run-method]");
    const startedEl = panel.querySelector("[data-run-started]");
    const finishedEl = panel.querySelector("[data-run-finished]");
    const durationEl = panel.querySelector("[data-run-duration]");
    const errorEl = panel.querySelector("[data-run-error]");
    const messageEl = panel.querySelector("[data-run-message]");
    const backupInfo = panel.querySelector("[data-backup-info]");
    const backupActions = panel.querySelector("[data-backup-actions]");
    if (!url || !statusEl || !messageEl) return;
    const apply = (data) => {
      statusEl.textContent = data.status || "-";
      statusEl.className = `badge status-${data.status || "unknown"}`;
      triggerEl.textContent = data.trigger_type || "-";
      methodEl.textContent = data.method || "-";
      startedEl.textContent = data.started_at || "-";
      finishedEl.textContent = data.finished_at || "-";
      durationEl.textContent = data.duration_ms === null || data.duration_ms === undefined ? "-" : `${data.duration_ms} ms`;
      errorEl.textContent = data.error_code || "-";
      messageEl.textContent = data.safe_message || "-";
      panel.dataset.currentStatus = data.status || "";
      if (backupInfo) {
        if (data.created_backup_uuid) {
          const size = data.created_backup_size ? ` (${data.created_backup_size})` : "";
          backupInfo.textContent = `${data.created_backup_name || data.created_backup_uuid}${size}`;
        } else {
          backupInfo.textContent = data.status === "failed" ? "Nenhum arquivo foi criado." : "-";
        }
      }
      if (backupActions) {
        if (data.created_backup_uuid) {
          backupActions.hidden = false;
          if (!backupActions.querySelector("[data-backup-href]")) {
            const detail = document.createElement("a");
            detail.className = "button secondary";
            detail.dataset.backupHref = "1";
            detail.textContent = "Ver backup";
            const download = document.createElement("a");
            download.className = "button secondary";
            download.dataset.backupHref = "1";
            download.textContent = "Baixar";
            backupActions.append(detail, download);
          }
          backupActions.querySelectorAll("[data-backup-href]").forEach((link, index) => {
            link.href = index === 0 ? `/backups/${data.created_backup_uuid}` : `/backups/${data.created_backup_uuid}/download`;
          });
        } else {
          backupActions.hidden = true;
        }
      }
      return finalStates.has(data.status);
    };
    const poll = () => {
      fetch(url, {headers: {"Accept": "application/json"}})
        .then((response) => response.ok ? response.json() : Promise.reject())
        .then((data) => {
          if (!apply(data)) window.setTimeout(poll, 4000);
        })
        .catch(() => window.setTimeout(poll, 6000));
    };
    if (!finalStates.has(panel.dataset.currentStatus || "")) window.setTimeout(poll, 3000);
  });
})();

(() => {
  const dialog = document.getElementById("mikrotik-manual-script");
  document.querySelectorAll("[data-credential-rotation-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      if (!dialog?.showModal) return;
      event.preventDefault();
      const source = dialog.querySelector("[data-manual-script-content]");
      const feedback = dialog.querySelector("[data-copy-feedback]");
      const copy = dialog.querySelector("[data-manual-copy]");
      if (source) source.textContent = "";
      if (copy) copy.disabled = true;
      if (feedback) feedback.textContent = "Atualizando a senha e preparando o script…";
      dialog.showModal();
      try {
        const response = await fetch(form.action, {method: "POST", headers: {"Accept": "text/plain"}});
        if (!response.ok || !(response.headers.get("Content-Type") || "").toLowerCase().includes("text/plain")) {
          throw new Error("Não foi possível atualizar a senha FTP.");
        }
        const script = await response.text();
        if (!source || !script) throw new Error("O servidor não retornou o script completo.");
        source.textContent = script;
        if (copy) copy.disabled = false;
        if (feedback) feedback.textContent = "Senha atualizada. Copie o novo script e aplique no MikroTik.";
      } catch (error) {
        if (feedback) feedback.textContent = error instanceof Error ? error.message : "Não foi possível atualizar a senha FTP.";
      }
    });
  });
})();

// Controlador isolado: o wizard deve continuar funcionando mesmo quando um
// componente opcional da tela de equipamento falhar durante a inicialização.
(() => {
  const render = (dialog, requestedStep) => {
    const steps = [...dialog.querySelectorAll("[data-config-step]")];
    if (!steps.length) return;
    const current = Math.max(1, Math.min(steps.length, requestedStep));
    dialog.dataset.configCurrentStep = String(current);
    steps.forEach((step) => { step.hidden = Number(step.dataset.configStep) !== current; });
    const back = dialog.querySelector("[data-config-back]");
    const next = dialog.querySelector("[data-config-next]");
    const submit = dialog.querySelector("[data-config-submit]");
    if (back) back.hidden = current === 1;
    if (next) next.hidden = current === steps.length;
    if (submit) submit.hidden = current !== steps.length;
    [...dialog.querySelectorAll("[data-config-step-marker]")].forEach((marker) => {
      const markerStep = Number(marker.dataset.configStepMarker);
      marker.classList.toggle("is-current", markerStep === current);
      marker.classList.toggle("is-complete", markerStep < current);
    });
    [...dialog.querySelectorAll(".mikrotik-config-stepper > i")].forEach((line, index) => {
      line.classList.toggle("is-complete", index + 1 < current);
    });
  };

  document.querySelectorAll("[data-mikrotik-config-wizard]").forEach((dialog) => {
    render(dialog, 1);
    dialog.addEventListener("click", (event) => {
      const button = event.target.closest("[data-config-next], [data-config-back]");
      if (!button) return;
      const current = Number(dialog.dataset.configCurrentStep || "1");
      if (button.matches("[data-config-next]")) {
        const active = dialog.querySelector(`[data-config-step="${current}"]`);
        const invalid = [...(active?.querySelectorAll("input, select") || [])]
          .filter((field) => !field.disabled).find((field) => !field.checkValidity());
        if (invalid) { invalid.reportValidity(); return; }
        render(dialog, current + 1);
      } else {
        render(dialog, current - 1);
      }
    });
    dialog.addEventListener("close", () => render(dialog, 1));
  });
})();

(() => {
  const page = document.querySelector("[data-password-change]");
  if (!page) return;
  const password = page.querySelector("[data-new-password]");
  const confirmation = page.querySelector("[data-password-confirm]");
  const strength = page.querySelector("[data-password-strength]");
  const bar = page.querySelector("[data-password-strength-bar]");
  const rules = {
    length: (value) => value.length >= 10,
    uppercase: (value) => /[A-ZÀ-Ý]/.test(value),
    number: (value) => /\d/.test(value),
    special: (value) => /[^A-Za-zÀ-ÿ0-9\s]/.test(value),
  };
  const update = () => {
    const value = password?.value || "";
    const passed = Object.entries(rules).filter(([key, test]) => {
      const valid = test(value);
      page.querySelector(`[data-password-rule="${key}"]`)?.classList.toggle("is-valid", valid);
      return valid;
    }).length;
    const labels = value ? ["Muito fraca", "Fraca", "Média", "Forte", "Forte"] : ["Digite a nova senha"];
    if (strength) strength.textContent = labels[value ? passed : 0];
    if (bar) { bar.style.width = `${passed * 25}%`; bar.style.background = passed < 2 ? "#d84b4b" : passed < 4 ? "#d99a16" : "#07884f"; }
    if (confirmation) confirmation.setCustomValidity(!confirmation.value || confirmation.value === value ? "" : "As senhas precisam ser iguais.");
  };
  page.querySelectorAll("[data-password-toggle]").forEach((button) => button.addEventListener("click", () => {
    const input = button.parentElement?.querySelector("input");
    if (!input) return;
    input.type = input.type === "password" ? "text" : "password";
    button.setAttribute("aria-label", input.type === "password" ? "Mostrar senha" : "Ocultar senha");
  }));
  password?.addEventListener("input", update);
  confirmation?.addEventListener("input", update);
  update();
})();
