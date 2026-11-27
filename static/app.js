/* FarmFinder Frontend Application Logic — 2-Stage Workflow (Vanilla JS) */

(() => {
  const state = {
    readyForLive: false,
    activeStageTab: 1,
    discoveryResult: null,
    discoveryFilter: "전체",
    selectedLeaderIdx: null,
    currentResult: null,
    accumulatedClues: [],
    appsScriptCode: "",
    isRunning: false,
  };

  const el = (id) => document.getElementById(id);

  function showToast(message, durationMs = 3400) {
    const toast = el("toast");
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(showToast._timer);
    showToast._timer = setTimeout(() => {
      toast.hidden = true;
    }, durationMs);
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function switchStageTab(stage) {
    state.activeStageTab = stage;
    const tab1 = el("tab-stage1-btn");
    const tab2 = el("tab-stage2-btn");
    const form1 = el("discovery-form");
    const form2 = el("investigate-form");

    if (stage === 1) {
      tab1.classList.add("active");
      tab1.setAttribute("aria-selected", "true");
      tab2.classList.remove("active");
      tab2.setAttribute("aria-selected", "false");
      form1.hidden = false;
      form2.hidden = true;
    } else {
      tab2.classList.add("active");
      tab2.setAttribute("aria-selected", "true");
      tab1.classList.remove("active");
      tab1.setAttribute("aria-selected", "false");
      form1.hidden = true;
      form2.hidden = false;
    }
  }

  async function refreshStatus() {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      state.readyForLive = Boolean(data.ready_for_live);
      state.appsScriptCode = data.apps_script_code || "";

      const apis = data.apis || {};
      const strip = el("api-status-strip");
      const items = [
        { label: "Gemini · 구글검색", ok: apis.gemini?.configured },
        { label: "네이버 검색", ok: apis.naver?.configured },
        { label: "GAP 인증", ok: apis.gap?.configured },
        { label: "도로명주소", ok: apis.juso?.configured },
        { label: "aT FIS", ok: true },
        { label: "Google 시트", ok: apis.sheets?.configured },
      ];
      strip.innerHTML = items
        .map(
          (item) => `
          <span class="status-tag ${item.ok ? "ready" : ""}">
            <span class="status-dot"></span>
            ${escapeHtml(item.label)}
          </span>`
        )
        .join("");

      if (apis.sheets?.sheet_url) {
        el("open-google-sheet-link").href = apis.sheets.sheet_url;
      }
      if (apis.sheets?.sheet_id) {
        el("cfg-sheet-id").value = apis.sheets.sheet_id;
      }
      if (apis.sheets?.sheet_tab) {
        el("cfg-sheet-tab").value = apis.sheets.sheet_tab;
      }
      if (apis.gemini?.model) {
        el("cfg-gemini-model").value = apis.gemini.model;
      }
      el("apps-script-snippet").textContent = state.appsScriptCode;

      const csvFiles = data.local_csv_files || [];
      el("local-csv-list-text").textContent = csvFiles.length
        ? `현재 등록된 로컬 CSV: ${csvFiles.join(", ")}`
        : "등록된 로컬 CSV 파일 없음 (실시간 API 조회만 사용)";

      const banner = el("mode-banner");
      const bannerText = el("mode-banner-text");
      const setupBtn = el("banner-setup-btn");

      if (state.readyForLive) {
        banner.classList.add("live");
        const missingOptional = [];
        if (!apis.naver?.configured) missingOptional.push("네이버 검색");
        if (!apis.gap?.configured) missingOptional.push("GAP API");
        if (!apis.juso?.configured) missingOptional.push("도로명주소 API");
        bannerText.textContent = missingOptional.length
          ? `실시간 Gemini + 구글 탐색 활성화됨 · 추가 연결 권장: ${missingOptional.join(", ")}`
          : "모든 핵심 API 연결 완료 — 실시간 [1단계 대표 발굴 → 2단계 세부주소 추적] 모드로 동작합니다.";
        setupBtn.textContent = "설정 관리";
      } else {
        banner.classList.remove("live");
        bannerText.textContent =
          "실시간 탐색을 시작하려면 우측 상단 [API · 시트 설정](또는 .env 파일)에 발급받은 API 키(Gemini, 네이버, GAP, 도로명주소)를 입력해 주세요.";
        setupBtn.textContent = "API 키 설정";
      }
    } catch (err) {
      console.error("Failed to fetch status:", err);
    }
  }

  async function refreshHistory() {
    try {
      const res = await fetch("/api/history");
      const data = await res.json();
      const records = data.records || [];
      const container = el("history-list");

      if (!records.length) {
        container.innerHTML = '<p class="panel-caption">아직 저장된 탐색 기록이 없습니다.</p>';
        return;
      }

      container.innerHTML = records
        .map((r) => {
          const title = r.farm_name || r.crop_group_name || r.representative || "미특정 결과";
          const addr = r.road_address || r.jibun_address || "권역 확인";
          const syncedBadge = r.synced_to_sheet_at ? " · 시트저장됨" : "";
          return `
            <div class="history-item" data-record-id="${r.id}">
              <div class="history-item-top">
                <span>${escapeHtml(title)}</span>
                <span style="font-size:0.74rem; color:var(--text-tertiary);">${escapeHtml(
                  (r.created_at || "").slice(5, 16)
                )}</span>
              </div>
              <div class="history-item-sub">
                ${escapeHtml(addr)}${r.phone ? " · " + escapeHtml(r.phone) : ""}${escapeHtml(syncedBadge)}
              </div>
            </div>
          `;
        })
        .join("");

      container.querySelectorAll(".history-item").forEach((node) => {
        node.addEventListener("click", () => loadHistoryRecord(node.dataset.recordId));
      });
    } catch (err) {
      console.error("Failed to load history:", err);
    }
  }

  async function loadHistoryRecord(recordId) {
    try {
      const res = await fetch(`/api/history/${recordId}`);
      if (!res.ok) return;
      const detail = await res.json();
      if (detail.raw_result && detail.raw_result.best_match) {
        detail.raw_result.record_id = detail.id;
        renderCompleteResult(detail.raw_result);
        el("res-memo").value = detail.memo || "";
        showToast("저장된 탐색 기록을 불러왔습니다.");
      }
    } catch (err) {
      console.error(err);
    }
  }

  function appendStepNode(listEl, prefix, evt) {
    const item = document.createElement("div");
    item.className = "trace-item running";
    item.id = `${prefix}-step-${evt.step_no}`;
    const argsPreview = Object.entries(evt.args || {})
      .filter(([, v]) => v)
      .map(([k, v]) => `${k}: ${v}`)
      .join(" | ");
    item.innerHTML = `
      <div class="trace-head">
        <div>
          <span class="trace-step-badge">STEP ${evt.step_no}</span>
          <span class="trace-tool-name">${escapeHtml(evt.tool_label)}</span>
        </div>
        <span class="spinner" aria-label="조회 중"></span>
      </div>
      <div class="trace-query">${escapeHtml(argsPreview)}</div>
    `;
    listEl.appendChild(item);
  }

  function finishStepNode(listEl, prefix, step) {
    let item = el(`${prefix}-step-${step.step_no}`);
    if (!item) {
      item = document.createElement("div");
      item.id = `${prefix}-step-${step.step_no}`;
      listEl.appendChild(item);
    }
    item.className = "trace-item";
    const linksHtml = (step.urls || [])
      .map(
        (u, idx) =>
          `<a href="${escapeHtml(u)}" target="_blank" rel="noopener noreferrer">출처 ${idx + 1} 열기</a>`
      )
      .join("");
    item.innerHTML = `
      <div class="trace-head">
        <div>
          <span class="trace-step-badge">STEP ${step.step_no}</span>
          <span class="trace-tool-name">${escapeHtml(step.tool_label)}</span>
        </div>
      </div>
      <div class="trace-query">${escapeHtml(step.query_summary)}</div>
      <div class="trace-finding">${escapeHtml(step.findings_summary)}</div>
      ${linksHtml ? `<div class="trace-links">${linksHtml}</div>` : ""}
    `;
  }

  // ==========================================================================
  // [1단계] 지역 + 품목 기반 대표 작목반 · 대표 농가 발굴
  // ==========================================================================

  function renderDiscoveryLeaders() {
    const disc = state.discoveryResult;
    if (!disc) return;

    const allLeaders = disc.leaders || [];
    const categories = ["전체", ...new Set(allLeaders.map((l) => l.leader_type).filter(Boolean))];

    el("discovery-filter-pills").innerHTML = categories
      .map(
        (cat) => `
        <button type="button" class="filter-pill ${state.discoveryFilter === cat ? "active" : ""}" data-cat="${escapeHtml(
          cat
        )}">
          ${escapeHtml(cat)} (${cat === "전체" ? allLeaders.length : allLeaders.filter((x) => x.leader_type === cat).length})
        </button>`
      )
      .join("");

    el("discovery-filter-pills")
      .querySelectorAll(".filter-pill")
      .forEach((btn) => {
        btn.addEventListener("click", () => {
          state.discoveryFilter = btn.dataset.cat;
          renderDiscoveryLeaders();
        });
      });

    const filtered = allLeaders
      .map((item, idx) => ({ item, idx }))
      .filter(({ item }) => state.discoveryFilter === "전체" || item.leader_type === state.discoveryFilter);

    const listEl = el("discovery-list");
    if (!filtered.length) {
      listEl.innerHTML = '<p class="panel-caption">발굴된 항목이 없습니다. 지역 범위나 품목명을 조금 넓혀서 다시 조회해 보세요.</p>';
      return;
    }

    listEl.innerHTML = filtered
      .map(({ item, idx }) => {
        const displayName = [item.crop_group_name, item.farm_name].filter(Boolean).join(" · ") || "미상 조직/농가";
        const isSelected = state.selectedLeaderIdx === idx;
        return `
          <div class="leader-row ${isSelected ? "selected-target" : ""}" id="leader-card-${idx}">
            <div style="flex: 1;">
              <div class="leader-meta-top">
                <span class="badge badge-verified">${escapeHtml(item.leader_type || "대표 후보")}</span>
                ${
                  item.scale_or_badge
                    ? `<span class="badge badge-precision">${escapeHtml(item.scale_or_badge)}</span>`
                    : ""
                }
              </div>
              <h3 class="leader-title">${escapeHtml(displayName)}</h3>
              <div class="leader-sub">
                <strong>권역:</strong> ${escapeHtml(item.sub_region || disc.region)}
                ${item.representative ? ` &nbsp;·&nbsp; <strong>대표/회장:</strong> ${escapeHtml(item.representative)}` : ""}
                ${item.phone ? ` &nbsp;·&nbsp; <strong>연락처:</strong> ${escapeHtml(item.phone)}` : ""}
              </div>
              ${item.summary ? `<div class="leader-summary">${escapeHtml(item.summary)}</div>` : ""}
              ${
                item.source_url
                  ? `<div class="trace-links">
                      <a href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener noreferrer">
                        [${escapeHtml(item.source_type)}] ${escapeHtml(item.source_title || "출처 확인")}
                      </a>
                    </div>`
                  : ""
              }
            </div>
            <div class="leader-actions">
              <button type="button" class="btn btn-primary drilldown-leader-btn" data-leader-idx="${idx}">
                세부주소 찾기 →
              </button>
              <button type="button" class="btn btn-secondary quick-save-leader-btn" data-leader-idx="${idx}">
                요약 시트 저장
              </button>
            </div>
          </div>
        `;
      })
      .join("");

    listEl.querySelectorAll(".drilldown-leader-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.dataset.leaderIdx);
        drillDownFromLeader(idx);
      });
    });

    listEl.querySelectorAll(".quick-save-leader-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.dataset.leaderIdx);
        quickSaveLeaderSummary(idx);
      });
    });
  }

  async function runStage1Discovery() {
    if (state.isRunning) return;
    const region = el("disc-region").value.trim();
    const cropItem = el("disc-crop-item").value.trim();
    const focusHint = el("disc-focus-hint").value.trim();

    if (!region || !cropItem) {
      showToast("초기 조건인 #지역과 #품목명을 모두 입력해 주세요.");
      (!region ? el("disc-region") : el("disc-crop-item")).focus();
      return;
    }

    state.isRunning = true;
    state.selectedLeaderIdx = null;
    state.discoveryFilter = "전체";
    const btn = el("start-discovery-btn");
    btn.disabled = true;
    btn.textContent = "지역 대표 작목반 · 농가 발굴 중...";

    el("empty-workbench").hidden = true;
    el("discovery-panel").hidden = true;
    el("discovery-trace-panel").hidden = false;
    el("discovery-trace-list").innerHTML = "";
    el("stage2-target-banner").hidden = true;
    el("clue-chain-panel").hidden = true;
    el("trace-panel").hidden = true;
    el("result-panel").hidden = true;
    el("candidates-panel").hidden = true;
    el("narrow-panel").hidden = true;

    try {
      const response = await fetch("/api/discover/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          region,
          crop_item: cropItem,
          focus_hint: focusHint,
          max_steps: 4,
        }),
      });

      if (!response.ok || !response.body) {
        throw new Error(`HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      const traceListEl = el("discovery-trace-list");

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";

        for (const chunk of parts) {
          const line = chunk.trim();
          if (!line.startsWith("data:")) continue;
          const evt = JSON.parse(line.slice(5).trim());

          if (evt.event === "status") {
            el("discovery-trace-status").textContent = evt.message;
          } else if (evt.event === "tool_start") {
            el("discovery-trace-status").textContent = `1단계 조회 중: ${evt.tool_label}...`;
            appendStepNode(traceListEl, "disc", evt);
          } else if (evt.event === "tool_end") {
            finishStepNode(traceListEl, "disc", evt.step);
          } else if (evt.event === "discovery_complete") {
            state.discoveryResult = evt.result;
            el("discovery-trace-panel").hidden = true;
            el("discovery-panel").hidden = false;
            el("discovery-body").hidden = false;
            el("discovery-panel-title").textContent = `[1단계 결과] '${evt.result.region} · ${evt.result.crop_item}' 대표 작목반 · 대표 농가 (${
              (evt.result.leaders || []).length
            }곳)`;
            el("discovery-overview").textContent = evt.result.overview_summary || "";
            renderDiscoveryLeaders();
          } else if (evt.event === "error") {
            el("discovery-trace-status").textContent = evt.message;
            showToast(evt.message, 5000);
          }
        }
      }
    } catch (err) {
      el("discovery-trace-status").textContent = `오류: ${err.message}`;
      showToast(`1단계 발굴 요청 실패: ${err.message}`);
    } finally {
      state.isRunning = false;
      btn.disabled = false;
      btn.textContent = "지역 대표 작목반 · 대표 농가 찾기";
    }
  }

  function drillDownFromLeader(idx) {
    const disc = state.discoveryResult;
    if (!disc || !disc.leaders || !disc.leaders[idx]) return;
    const target = disc.leaders[idx];
    state.selectedLeaderIdx = idx;
    renderDiscoveryLeaders();

    // 2단계 폼에 선택한 대표 작목반·농가 단서를 자동 기입
    el("input-region").value = target.sub_region || disc.region || "";
    el("input-crop-item").value = target.crop_item || disc.crop_item || "";
    el("input-crop-group").value = target.crop_group_name || "";
    el("input-farm-person").value = target.farm_name || "";
    el("input-representative").value = target.representative || "";
    el("input-extra-clue").value = target.summary ? `1단계 확인 단서: ${target.summary}` : "";

    const labelParts = [target.crop_group_name, target.farm_name, target.representative]
      .filter(Boolean)
      .join(" · ");
    el("stage2-target-banner").hidden = false;
    el("stage2-target-text").textContent = `2단계 세부주소 추적 대상: ${labelParts} (${target.sub_region || disc.region})`;

    runInvestigation(false, true);
  }

  async function quickSaveLeaderSummary(idx) {
    const disc = state.discoveryResult;
    if (!disc || !disc.leaders || !disc.leaders[idx]) return;
    const item = disc.leaders[idx];

    const payload = {
      record_id: null,
      input_summary: `1단계 대표발굴 | 지역:${disc.region} | 품목:${disc.crop_item}`,
      crop_group_name: item.crop_group_name || "",
      farm_name: item.farm_name || "",
      representative: item.representative || "",
      crop_item: item.crop_item || disc.crop_item || "",
      road_address: "",
      jibun_address: item.sub_region || disc.region || "",
      postal_code: "",
      phone: item.phone || "",
      verification_status: `1단계 ${item.leader_type} 확인`,
      reasoning_summary: item.summary || "",
      source_urls: item.source_url || "",
      memo: item.scale_or_badge || "",
    };

    try {
      const res = await fetch("/api/sheets/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.ok) {
        showToast("선택한 대표 작목반·농가 요약 정보가 Google 시트에 저장되었습니다!");
      } else {
        showToast("로컬 이력에 저장되었습니다. (시트 자동 기록은 우측 상단 설정에서 웹훅 연결 시 활성화)", 4500);
      }
      refreshHistory();
    } catch (err) {
      showToast(`저장 실패: ${err.message}`);
    }
  }

  // ==========================================================================
  // [2단계] 세부주소 · 전화번호 꼬리물기 추적
  // ==========================================================================

  function resetStage2TraceUI(keepDiscoveryVisible) {
    el("empty-workbench").hidden = true;
    if (!keepDiscoveryVisible && !state.discoveryResult) {
      el("discovery-panel").hidden = true;
      el("stage2-target-banner").hidden = true;
    }
    el("trace-panel").hidden = false;
    el("trace-list").innerHTML = "";
    el("clue-chain-panel").hidden = true;
    el("result-panel").hidden = true;
    el("candidates-panel").hidden = true;
    el("narrow-panel").hidden = true;
  }

  function fillCandidateForm(candidate, modeUsed) {
    el("res-crop-group").value = candidate.crop_group_name || "";
    el("res-farm-name").value = candidate.farm_name || "";
    el("res-representative").value = candidate.representative || "";
    el("res-crop-item").value = candidate.crop_item || "";
    el("res-road-address").value = candidate.road_address || "";
    el("res-jibun-address").value = candidate.jibun_address || "";
    el("res-postal-code").value = candidate.postal_code || "";
    el("res-phone").value = candidate.phone || "";

    el("result-reasoning").textContent = candidate.reasoning_summary || "";

    const badges = [];
    if (candidate.verification_status) {
      badges.push(`<span class="badge badge-verified">${escapeHtml(candidate.verification_status)}</span>`);
    }
    if (candidate.address_precision) {
      badges.push(`<span class="badge badge-precision">${escapeHtml(candidate.address_precision)}</span>`);
    }
    if (candidate.confidence) {
      badges.push(`<span class="badge badge-verified">신뢰도 ${candidate.confidence}%</span>`);
    }
    el("result-badges").innerHTML = badges.join("");

    const sources = candidate.sources || [];
    const srcContainer = el("result-sources");
    if (!sources.length) {
      srcContainer.innerHTML = '<span class="panel-caption">별도 등록된 출처 URL이 없습니다.</span>';
    } else {
      srcContainer.innerHTML = sources
        .map(
          (s) => `
          <div class="source-row">
            <div>
              <strong>[${escapeHtml(s.source_type || "출처")}]</strong>
              ${
                s.url
                  ? `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(
                      s.title || s.url
                    )}</a>`
                  : escapeHtml(s.title)
              }
              ${s.matched_info ? `<div style="color:var(--text-secondary); margin-top:2px;">${escapeHtml(s.matched_info)}</div>` : ""}
            </div>
          </div>`
        )
        .join("");
    }
  }

  function renderCompleteResult(result) {
    state.currentResult = result;
    el("empty-workbench").hidden = true;

    const traceListEl = el("trace-list");
    el("trace-panel").hidden = false;
    el("trace-status-text").textContent = `총 ${(result.trace_steps || []).length}단계 도구 호출 및 교차 대조 완료`;
    traceListEl.innerHTML = "";
    (result.trace_steps || []).forEach((st) => finishStepNode(traceListEl, "inv", st));

    const clues = result.discovered_clues || [];
    state.accumulatedClues = clues.map((c) => `${c.clue_type}:${c.value}`);
    if (clues.length) {
      el("clue-chain-panel").hidden = false;
      el("clue-chain-list").innerHTML = clues
        .map(
          (c, i) => `
          ${i > 0 ? '<span class="clue-arrow">→</span>' : ""}
          <button type="button" class="clue-badge" data-clue-val="${escapeHtml(c.value)}" title="${escapeHtml(
            c.found_via
          )}">
            <span class="clue-badge-type">${escapeHtml(c.clue_type)}</span>
            <span>${escapeHtml(c.value)}</span>
          </button>`
        )
        .join("");

      el("clue-chain-list")
        .querySelectorAll(".clue-badge")
        .forEach((btn) => {
          btn.addEventListener("click", () => {
            const cur = el("narrow-extra-input").value.trim();
            const val = btn.dataset.clueVal;
            el("narrow-extra-input").value = cur ? `${cur} ${val}` : `'${val}' 단서를 중심으로 세부번지·연락처 추가 확인`;
            el("narrow-extra-input").focus();
          });
        });
    } else {
      el("clue-chain-panel").hidden = true;
    }

    el("result-panel").hidden = false;
    el("save-feedback-text").textContent = result.record_id
      ? `로컬 탐색 이력 #${result.record_id} 자동 저장됨`
      : "";
    fillCandidateForm(result.best_match || {}, result.mode_used);

    const candidates = result.candidates || [];
    if (candidates.length) {
      el("candidates-panel").hidden = false;
      el("candidates-list").innerHTML = candidates
        .map(
          (c, idx) => `
          <div class="candidate-item">
            <div>
              <strong>${escapeHtml(c.farm_name || c.crop_group_name || "후보 " + (idx + 1))}</strong>
              ${c.representative ? ` · 대표 ${escapeHtml(c.representative)}` : ""}
              <div style="font-size:0.8rem; color:var(--text-secondary); margin-top:2px;">
                ${escapeHtml(c.road_address || c.jibun_address || "주소 미상")}
                ${c.phone ? ` · ${escapeHtml(c.phone)}` : ""}
              </div>
            </div>
            <div style="display:flex; gap:6px;">
              <button type="button" class="btn btn-ghost select-cand-btn" data-cand-idx="${idx}">이 후보 선택</button>
              <button type="button" class="btn btn-ghost narrow-cand-btn" data-cand-name="${escapeHtml(
                c.farm_name || c.representative
              )}">이 이름으로 좁히기</button>
            </div>
          </div>`
        )
        .join("");

      el("candidates-list")
        .querySelectorAll(".select-cand-btn")
        .forEach((btn) => {
          btn.addEventListener("click", () => {
            const chosen = candidates[Number(btn.dataset.candIdx)];
            if (chosen) {
              fillCandidateForm(chosen, result.mode_used);
              showToast("선택한 후보 정보를 편집 영역에 반영했습니다.");
            }
          });
        });

      el("candidates-list")
        .querySelectorAll(".narrow-cand-btn")
        .forEach((btn) => {
          btn.addEventListener("click", () => {
            const name = btn.dataset.candName || "";
            el("input-farm-person").value = name;
            el("narrow-extra-input").value = `'${name}'의 정확한 세부 번지와 연락처를 집중 추적`;
            runInvestigation(true, true);
          });
        });
    } else {
      el("candidates-panel").hidden = true;
    }

    el("narrow-panel").hidden = false;
    const suggestions = result.follow_up_suggestions || [];
    el("narrow-suggestions").innerHTML = suggestions
      .map((s) => `<button type="button" class="preset-chip narrow-sug-chip">${escapeHtml(s)}</button>`)
      .join("");
    el("narrow-suggestions")
      .querySelectorAll(".narrow-sug-chip")
      .forEach((chip) => {
        chip.addEventListener("click", () => {
          el("narrow-extra-input").value = chip.textContent;
          runInvestigation(true, true);
        });
      });

    refreshHistory();
  }

  async function runInvestigation(isFollowUp = false, fromDiscovery = false) {
    if (state.isRunning) return;

    const payload = {
      region: el("input-region").value.trim(),
      crop_item: el("input-crop-item").value.trim(),
      crop_group: el("input-crop-group").value.trim(),
      farm_or_person: el("input-farm-person").value.trim(),
      representative: el("input-representative").value.trim(),
      extra_clue: isFollowUp
        ? [el("input-extra-clue").value.trim(), el("narrow-extra-input").value.trim()].filter(Boolean).join(" / ")
        : el("input-extra-clue").value.trim(),
      prior_clues: isFollowUp ? state.accumulatedClues : [],
      max_steps: Number(el("select-max-steps").value || 6),
    };

    if (
      !payload.region &&
      !payload.crop_item &&
      !payload.crop_group &&
      !payload.farm_or_person &&
      !payload.representative &&
      !payload.extra_clue
    ) {
      showToast("최소 1개 이상의 단서를 입력해 주세요.");
      el("input-region").focus();
      return;
    }

    state.isRunning = true;
    const startBtn = el("start-investigate-btn");
    startBtn.disabled = true;
    startBtn.textContent = "세부주소 꼬리물기 진행 중...";
    resetStage2TraceUI(fromDiscovery);

    if (fromDiscovery) {
      el("trace-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    }

    try {
      const response = await fetch("/api/investigate/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!response.ok || !response.body) {
        throw new Error(`HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      const traceListEl = el("trace-list");

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";

        for (const chunk of parts) {
          const line = chunk.trim();
          if (!line.startsWith("data:")) continue;
          const jsonStr = line.slice(5).trim();
          if (!jsonStr) continue;

          const evt = JSON.parse(jsonStr);
          if (evt.event === "status") {
            el("trace-status-text").textContent = evt.message;
          } else if (evt.event === "tool_start") {
            el("trace-status-text").textContent = `${evt.step_no}단계 실행 중: ${evt.tool_label}...`;
            appendStepNode(traceListEl, "inv", evt);
          } else if (evt.event === "tool_end") {
            finishStepNode(traceListEl, "inv", evt.step);
          } else if (evt.event === "complete") {
            renderCompleteResult(evt.result);
            el("narrow-extra-input").value = "";
          } else if (evt.event === "error") {
            el("trace-status-text").textContent = evt.message;
            showToast(evt.message, 5000);
          }
        }
      }
    } catch (err) {
      el("trace-status-text").textContent = `오류 발생: ${err.message}`;
      showToast(`탐색 요청 실패: ${err.message}`);
    } finally {
      state.isRunning = false;
      startBtn.disabled = false;
      startBtn.textContent = "세부주소 · 연락처 꼬리물기 탐색 시작";
    }
  }

  function collectSavePayload() {
    const cur = state.currentResult || {};
    const sources = cur.best_match?.sources || [];
    return {
      record_id: cur.record_id || null,
      input_summary: cur.input_summary || "",
      crop_group_name: el("res-crop-group").value.trim(),
      farm_name: el("res-farm-name").value.trim(),
      representative: el("res-representative").value.trim(),
      crop_item: el("res-crop-item").value.trim(),
      road_address: el("res-road-address").value.trim(),
      jibun_address: el("res-jibun-address").value.trim(),
      postal_code: el("res-postal-code").value.trim(),
      phone: el("res-phone").value.trim(),
      verification_status: cur.best_match?.verification_status || "검수완료",
      reasoning_summary: el("result-reasoning").textContent.trim(),
      source_urls: sources.map((s) => s.url).filter(Boolean).join("\n"),
      memo: el("res-memo").value.trim(),
    };
  }

  async function saveResultToSheet(syncSheet) {
    const payload = collectSavePayload();
    const btn = el("save-to-sheet-btn");
    btn.disabled = true;

    try {
      const res = await fetch("/api/sheets/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();

      if (!syncSheet) {
        el("save-feedback-text").textContent = "보정한 내용이 로컬 이력 및 CSV에 반영되었습니다.";
        showToast("로컬 탐색 이력 업데이트 완료");
      } else if (data.ok) {
        el("save-feedback-text").textContent = "FarmFinder Google 시트에 새 행이 추가되었습니다.";
        showToast("Google 시트 저장 완료!");
      } else {
        el("save-feedback-text").textContent = data.error || "시트 저장 설정이 필요합니다.";
        showToast(data.error || "시트 연동 설정을 확인해 주세요.", 5200);
        if (data.needs_setup) {
          el("settings-modal").hidden = false;
        }
      }
      refreshHistory();
    } catch (err) {
      showToast(`저장 중 오류: ${err.message}`);
    } finally {
      btn.disabled = false;
    }
  }

  function wireEvents() {
    el("tab-stage1-btn").addEventListener("click", () => switchStageTab(1));
    el("tab-stage2-btn").addEventListener("click", () => switchStageTab(2));

    el("discovery-form").addEventListener("submit", (e) => {
      e.preventDefault();
      runStage1Discovery();
    });

    el("investigate-form").addEventListener("submit", (e) => {
      e.preventDefault();
      el("stage2-target-banner").hidden = true;
      runInvestigation(false, false);
    });

    el("toggle-discovery-body-btn").addEventListener("click", () => {
      const body = el("discovery-body");
      body.hidden = !body.hidden;
    });

    el("back-to-discovery-btn").addEventListener("click", () => {
      el("discovery-body").hidden = false;
      el("discovery-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    });

    el("run-narrow-btn").addEventListener("click", () => runInvestigation(true, true));
    el("narrow-extra-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        runInvestigation(true, true);
      }
    });

    el("reset-form-btn").addEventListener("click", () => {
      el("discovery-form").reset();
      el("investigate-form").reset();
      state.accumulatedClues = [];
    });

    document.querySelectorAll(".copy-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const target = el(btn.dataset.copyTarget);
        if (target && target.value) {
          await navigator.clipboard.writeText(target.value);
          showToast("클립보드에 복사했습니다.");
        }
      });
    });

    el("save-to-sheet-btn").addEventListener("click", () => saveResultToSheet(true));
    el("save-local-only-btn").addEventListener("click", () => saveResultToSheet(false));
    el("refresh-history-btn").addEventListener("click", refreshHistory);

    const openModal = () => {
      el("settings-modal").hidden = false;
    };
    const closeModal = () => {
      el("settings-modal").hidden = true;
    };
    el("open-settings-btn").addEventListener("click", openModal);
    el("banner-setup-btn").addEventListener("click", openModal);
    el("close-settings-btn").addEventListener("click", closeModal);
    el("cancel-settings-btn").addEventListener("click", closeModal);

    el("copy-apps-script-btn").addEventListener("click", async () => {
      await navigator.clipboard.writeText(state.appsScriptCode);
      showToast("Apps Script 코드가 복사되었습니다. 시트의 확장 프로그램 > Apps Script에 붙여넣으세요.");
    });

    el("settings-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const updates = {
        GEMINI_API_KEY: el("cfg-gemini-key").value,
        GEMINI_MODEL: el("cfg-gemini-model").value,
        NAVER_CLIENT_ID: el("cfg-naver-id").value,
        NAVER_CLIENT_SECRET: el("cfg-naver-secret").value,
        DATA_GO_KR_API_KEY: el("cfg-gap-key").value,
        JUSO_API_KEY: el("cfg-juso-key").value,
        SERPER_API_KEY: el("cfg-serper-key").value,
        GOOGLE_SHEET_ID: el("cfg-sheet-id").value,
        GOOGLE_SHEET_TAB: el("cfg-sheet-tab").value,
        GOOGLE_APPS_SCRIPT_WEBHOOK_URL: el("cfg-webhook-url").value,
        GOOGLE_SERVICE_ACCOUNT_FILE: el("cfg-sa-file").value,
      };
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ updates }),
      });
      if (res.ok) {
        ["cfg-gemini-key", "cfg-naver-secret", "cfg-gap-key", "cfg-juso-key", "cfg-serper-key"].forEach((id) => {
          el(id).value = "";
        });
        closeModal();
        await refreshStatus();
        showToast("API 키 및 시트 설정이 .env에 저장되었습니다.");
      }
    });

    el("upload-csv-btn").addEventListener("click", async () => {
      const fileInput = el("csv-upload-file");
      if (!fileInput.files || !fileInput.files[0]) {
        showToast("업로드할 CSV 파일을 먼저 선택해 주세요.");
        return;
      }
      const kind = el("csv-upload-kind").value;
      const formData = new FormData();
      formData.append("file", fileInput.files[0]);
      const res = await fetch(`/api/upload/csv?kind=${encodeURIComponent(kind)}`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (data.ok) {
        fileInput.value = "";
        await refreshStatus();
        showToast(`${data.saved_as} (${data.size_kb} KB) 로컬 DB 등록 완료!`);
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    wireEvents();
    refreshStatus();
    refreshHistory();
  });
})();
