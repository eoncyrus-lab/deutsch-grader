var state = { user: null, csrf: null, submissions: [], current: null };

var STATUS = {
  uploaded: "待确认图片", transcribing: "转写中", awaiting_confirmation: "待确认转写",
  queued: "排队中", grading: "批改中", ready_for_review: "待终审",
  needs_manual: "需人工", finalized: "已完成", deleted: "已删除"
};

function $(selector) { return document.querySelector(selector); }

function esc(value) {
  return String(value === null || value === undefined ? "" : value).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function fmt(value) {
  if (value === null || value === undefined) return "—";
  return Number.isInteger(value) ? String(value) : Number(value).toFixed(1);
}

function pill(status) {
  var cls = status === "needs_manual" ? "danger" : ["awaiting_confirmation", "uploaded"].indexOf(status) >= 0 ? "warn" : status === "finalized" ? "" : "muted";
  return '<span class="pill ' + cls + '">' + esc(STATUS[status] || status) + "</span>";
}

function toast(message, bad) {
  var el = $("#toast");
  el.textContent = message;
  el.style.background = bad ? "var(--danger)" : "var(--ink)";
  el.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(function () { el.classList.add("hidden"); }, 3600);
}

document.addEventListener("dg-tts-warning", function (event) {
  toast(event.detail || "语音朗读不可用", true);
});

async function api(path, options) {
  options = options || {};
  var opts = { credentials: "same-origin", method: options.method || "GET", headers: options.headers || {} };
  if (options.body && !(options.body instanceof FormData)) opts.headers["Content-Type"] = "application/json";
  if (opts.method !== "GET") opts.headers["X-CSRF-Token"] = state.csrf || "";
  if (options.body !== undefined) opts.body = options.body;
  var response = await fetch(path, opts);
  var text = await response.text();
  var data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
  if (!response.ok) {
    var detail = data && data.detail ? data.detail : (text || response.statusText);
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

async function boot() {
  try {
    var data = await api("/api/me");
    state.user = data.user;
    state.csrf = data.user.csrf;
    await enterApp();
  } catch (e) {
    $("#login").classList.remove("hidden");
  }
}

function roleName(role) {
  var names = { teacher: "教师", rubric_owner: "量规负责人", admin: "管理员", dpo: "数据保护", student: "学生" };
  return names[role] || role;
}

async function enterApp() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#user-name").textContent = state.user.display_name + " · " + roleName(state.user.role);
  if (["rubric_owner", "admin", "dpo"].indexOf(state.user.role) >= 0) $("#metrics-btn").classList.remove("hidden");
  await loadSubmissions();
  if (state.submissions.length) openSubmission(state.submissions[0].id);
}

$("#login-form").addEventListener("submit", async function (event) {
  event.preventDefault();
  $("#login-error").textContent = "";
  var form = new FormData(event.target);
  try {
    var data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: form.get("email"), password: form.get("password") })
    });
    state.user = data.user;
    state.csrf = data.user.csrf;
    await enterApp();
  } catch (error) {
    $("#login-error").textContent = error.message;
  }
});

$("#logout-btn").addEventListener("click", async function () {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (e) {}
  location.reload();
});

$("#refresh-btn").addEventListener("click", function () {
  loadSubmissions();
  if (state.current) openSubmission(state.current.submission.id);
});

$("#new-btn").addEventListener("click", showCreate);
$("#metrics-btn").addEventListener("click", showMetrics);

window.addEventListener("hashchange", function () {
  var match = location.hash.match(/^#\/submission\/(.+)$/);
  if (match) openSubmission(match[1]);
});

async function loadSubmissions() {
  state.submissions = await api("/api/submissions");
  renderList();
}

function renderList() {
  var box = $("#submission-list");
  box.innerHTML = state.submissions.map(function (s) {
    var active = state.current && state.current.submission.id === s.id ? " active" : "";
    return '<button class="submission-item' + active + '" data-id="' + esc(s.id) + '">' +
      '<div class="row"><span class="name">' + esc(s.student || "未命名") + "</span>" + pill(s.status) + "</div>" +
      '<div class="row small muted"><span>' + esc(s.source.indexOf("demo:") === 0 ? "离线演示" : s.task_id) + "</span><span>" + (s.final_total !== null && s.final_total !== undefined ? s.final_total : (s.ai_total !== null && s.ai_total !== undefined ? s.ai_total : "")) + "</span></div>" +
      "</button>";
  }).join("");
  Array.prototype.forEach.call(box.querySelectorAll("button"), function (button) {
    button.addEventListener("click", function () { openSubmission(button.dataset.id); });
  });
}

async function openSubmission(id) {
  try {
    var detail = await api("/api/submissions/" + id);
    state.current = detail;
    location.hash = "#/submission/" + id;
    renderList();
    renderDetail(detail);
  } catch (error) {
    toast(error.message, true);
  }
}

async function showCreate() {
  try {
    var students = await api("/api/students");
    var configs = await api("/api/configs");
    var taskConfigId = configs.length ? configs[0].id : "";
    var options = students.map(function (s) {
      return '<option value="' + esc(s.id) + '">' + esc(s.pseudonym) + " · " + esc(s.class_code || "") + (s.active_consents ? "" : " · 无同意") + "</option>";
    }).join("");
    $("#content").innerHTML = '<div class="page-head"><div><h1>新建作文批改</h1><div class="meta muted small">输入转写稿或上传手写照片；教师端仍可逐条审核。</div></div></div>' +
      '<div class="card"><form id="create-form">' +
      '<label>学生<select name="student_id">' + options + "</select></label>" +
      '<label>标题<input name="title" placeholder="例如 Meine beste Reise"></label>' +
      '<label>作文文本（保留学生原有拼写和错误）<textarea name="text" rows="12" required placeholder="Meine beste Reise war im Sommer ..."></textarea></label>' +
      '<div class="actions"><button class="primary" type="submit">提交并批改</button><button type="button" id="cancel-create">取消</button></div>' +
      '<p class="muted small" id="create-status"></p></form></div>' +
      '<div class="card"><div class="card-head"><h2>拍照作文</h2><span class="pill warn">先脱敏，再转写</span></div>' +
      '<div class="mode-note">上传原始照片后，系统不会直接把它们发给模型。你需要为每张照片上传一张遮住姓名/班级/学校区域的副本，确认后才会转写。</div>' +
      '<form id="image-form">' +
      '<label>学生<select name="student_id">' + options + "</select></label>" +
      '<label>原始照片（可多选）<input name="files" type="file" accept="image/jpeg,image/png,image/webp" multiple required></label>' +
      '<input name="task_config_id" type="hidden" value="' + esc(taskConfigId) + '">' +
      '<div class="actions"><button class="primary" type="submit">上传照片</button></div>' +
      '<p class="muted small" id="image-status"></p></form></div>';
    $("#cancel-create").addEventListener("click", function () { if (state.current) renderDetail(state.current); else loadSubmissions(); });
    $("#create-form").addEventListener("submit", submitCreate);
    $("#image-form").addEventListener("submit", submitImageCreate);
  } catch (error) {
    toast(error.message, true);
  }
}

async function submitCreate(event) {
  event.preventDefault();
  var form = new FormData(event.target);
  var status = $("#create-status");
  status.textContent = "提交中…";
  try {
    var created = await api("/api/submissions/text", {
      method: "POST",
      body: JSON.stringify({
        student_id: form.get("student_id"),
        title: form.get("title") || null,
        text: form.get("text")
      })
    });
    status.textContent = "已排队，等待 worker 批改…";
    await loadSubmissions();
    await pollSubmission(created.submission_id);
  } catch (error) {
    status.textContent = "";
    toast(error.message, true);
  }
}

async function submitImageCreate(event) {
  event.preventDefault();
  var form = new FormData(event.target);
  var files = form.getAll("files").filter(function (file) { return file && file.size; });
  var status = $("#image-status");
  if (!files.length) {
    toast("请选择至少一张照片", true);
    return;
  }
  if (!form.get("task_config_id")) {
    toast("没有可用题型配置，请先初始化", true);
    return;
  }
  status.textContent = "上传中…";
  var body = new FormData();
  body.append("student_id", form.get("student_id"));
  body.append("task_config_id", form.get("task_config_id"));
  files.forEach(function (file) { body.append("files", file); });
  try {
    var created = await api("/api/submissions", { method: "POST", body: body });
    toast("照片已加密上传");
    await loadImageSetup(created.submission_id);
  } catch (error) {
    status.textContent = "";
    toast(error.message, true);
  }
}

async function loadImageSetup(submissionId) {
  try {
    var detail = await api("/api/submissions/" + submissionId);
    state.current = detail;
    await loadSubmissions();
    var images = (detail.images || []).filter(function (image) { return !image.deleted_at; });
    var rows = images.map(function (image) {
      var safe = image.redacted_at ? '<span class="pill">已脱敏</span>' :
        '<div><input type="file" accept="image/jpeg,image/png,image/webp" data-file="' + esc(image.id) + '">' +
        '<div class="actions"><button class="small" data-redact="' + esc(image.id) + '">上传脱敏副本</button></div></div>';
      return '<div class="image-row"><div><img class="thumb" src="/api/submissions/' + esc(submissionId) + '/images/' + esc(image.id) + '" alt="作文照片"></div>' +
        '<div><strong>' + esc(image.content_type) + "</strong><div class=\"hint\">" + Math.round(image.bytes / 1024) + ' KB</div>' + safe + "</div></div>";
    }).join("");
    var allRedacted = images.length > 0 && images.every(function (image) { return !!image.redacted_at; });
    var action = allRedacted ? '<div class="actions"><button class="primary" id="transcribe-btn">开始转写</button></div>' :
      '<p class="muted small">每张照片都上传脱敏副本后，才能开始转写。</p>';
    $("#content").innerHTML = pageHead(detail) +
      '<div class="card"><div class="card-head"><h2>照片与脱敏</h2><span class="pill muted">' + images.length + ' 张</span></div>' +
      (rows || '<p class="muted">还没有照片。</p>') + action + "</div>";
    bindProfile(detail);
    Array.prototype.forEach.call(document.querySelectorAll("[data-redact]"), function (button) {
      button.addEventListener("click", function () {
        var imageId = button.dataset.redact;
        var input = document.querySelector('input[data-file="' + imageId + '"]');
        uploadRedacted(submissionId, imageId, input && input.files ? input.files[0] : null);
      });
    });
    var transcribe = $("#transcribe-btn");
    if (transcribe) transcribe.addEventListener("click", function () { startTranscription(submissionId); });
    if (detail.submission.status === "transcribing") {
      pollSubmission(submissionId, ["awaiting_confirmation", "needs_manual"]);
    }
  } catch (error) {
    toast(error.message, true);
  }
}

async function uploadRedacted(submissionId, imageId, file) {
  if (!file) {
    toast("请选择遮住姓名的图片", true);
    return;
  }
  var body = new FormData();
  body.append("file", file);
  try {
    await api("/api/submissions/" + submissionId + "/images/" + imageId + "/redacted-copy", { method: "POST", body: body });
    toast("脱敏副本已上传");
    await loadImageSetup(submissionId);
  } catch (error) {
    toast(error.message, true);
  }
}

async function startTranscription(submissionId) {
  try {
    await api("/api/submissions/" + submissionId + "/transcribe", { method: "POST" });
    toast("已开始转写");
    await pollSubmission(submissionId, ["awaiting_confirmation", "needs_manual"]);
  } catch (error) {
    toast(error.message, true);
  }
}

async function pollSubmission(id, targets) {
  targets = targets || ["ready_for_review", "needs_manual", "finalized"];
  for (var i = 0; i < 45; i++) {
    var detail = await api("/api/submissions/" + id);
    state.current = detail;
    renderDetail(detail);
    if (targets.indexOf(detail.submission.status) >= 0) {
      toast(detail.submission.status === "awaiting_confirmation" ? "转写完成，请确认" : "批改完成，可以审核了");
      await loadSubmissions();
      return;
    }
    await new Promise(function (resolve) { setTimeout(resolve, 2000); });
  }
  toast("仍在排队；请确认 grading-worker 已启动", true);
}

function pageHead(d) {
  var s = d.submission;
  var demo = s.source && s.source.indexOf("demo:") === 0 ? '<span class="pill muted">离线演示</span>' : "";
  var canExport = d.run && (d.run.status === "ready_for_review" || d.run.status === "finalized");
  var exportLink = canExport ? '<a class="pill muted" href="/api/submissions/' + esc(s.id) + '/export?format=html" target="_blank" rel="noopener">学生版反馈</a>' : "";
  var profileButton = s.student_id ? '<button class="ghost" id="profile-btn">学生画像</button>' : "";
  return '<div class="page-head"><div><h1>' + esc(s.student || "未命名") + " · " + esc(s.task) + "</h1>" +
    '<div class="meta">' + pill(s.status) + demo + '<span class="muted small">' + esc(s.level) + " · " + esc(s.created_at) + "</span></div></div>" +
    '<div class="actions">' + exportLink + profileButton + "</div></div>";
}

function renderDetail(d) {
  var run = d.run;
  var scores = d.scores || {};
  var main = scores.final || scores.reviewed || scores.ai || {};
  var head = pageHead(d);

  if (d.can_confirm) {
    $("#content").innerHTML = head + confirmView(d);
    bindProfile(d);
    $("#confirm-form").addEventListener("submit", function (event) { submitConfirm(event, d); });
    return;
  }
  if (!run) {
    $("#content").innerHTML = head + '<div class="card">这份作业还没有批改结果。</div>';
    bindProfile(d);
    return;
  }
  if (run.status === "needs_manual") {
    $("#content").innerHTML = head +
      '<div class="card"><h2>需要人工处理</h2><p class="error">' + esc(run.error || "流水线未完成") + "</p>" +
      '<p class="muted small">不会展示部分分数。请修正转写或补全要素后重试。</p></div>' +
      transcriptCard(d) + checklistCard(d) + errorsCard(d);
    bindProfile(d);
    bindChecklist(d);
    bindErrors(d);
    return;
  }
  $("#content").innerHTML = head +
    '<div class="grid-2"><div>' + errorsCard(d) + checklistCard(d) + transcriptCard(d) + "</div>" +
    "<div>" + scoreCard(d, scores, main) + commentsCard(d) + "</div></div>";
  bindProfile(d);
  bindErrors(d);
  bindChecklist(d);
  bindFinalize(d);
}

function bindProfile(d) {
  var button = $("#profile-btn");
  if (button && d.submission.student_id) {
    button.addEventListener("click", function () { showProfile(d.submission.student_id); });
  }
}

function confirmView(d) {
  var t = d.transcript;
  var lines = t ? t.lines.map(function (line) { return line.text; }).join("\n") : "";
  var title = t && t.title ? t.title : "";
  return '<div class="card"><div class="card-head"><h2>确认转写</h2><span class="pill warn">教师确认后才进入批改</span></div>' +
    '<p class="muted small">保留学生原有拼写和语法错误；看不清的词保留 [?]。确认后会按句切分并固定转写版本。</p>' +
    '<form id="confirm-form"><label>标题<input name="title" value="' + esc(title) + '" placeholder="没有标题可留空"></label>' +
    '<label>逐行转写（一行一句或一行一段）<textarea name="lines" rows="9">' + esc(lines) + "</textarea></label>" +
    '<div class="actions"><button class="primary" type="submit">确认并开始批改</button></div></form></div>';
}

async function submitConfirm(event, d) {
  event.preventDefault();
  var form = new FormData(event.target);
  var lines = String(form.get("lines") || "").split("\n").map(function (text) { return { text: text }; }).filter(function (line) { return line.text.trim(); });
  try {
    await api("/api/submissions/" + d.submission.id + "/confirm", {
      method: "POST",
      body: JSON.stringify({ title: form.get("title") || null, lines: lines, illegible: [] })
    });
    toast("已进入批改队列");
    await openSubmission(d.submission.id);
  } catch (error) {
    toast(error.message, true);
  }
}

function transcriptCard(d) {
  if (!d.transcript) return "";
  var body = (d.transcript.title ? d.transcript.title + "\n" : "") + d.transcript.lines.map(function (line) { return line.text; }).join("\n");
  var sentences = d.sentences || [];
  var all = sentences.map(function (sentence) { return sentence.text; }).join(" ");
  var rows = sentences.map(function (sentence) {
    return '<div class="sentence-row"><span class="muted small">' + esc(sentence.sid) + "</span><span>" + esc(sentence.text) +
      '</span><button class="speak small" data-speak="' + esc(sentence.text) + '" title="朗读这句德语">🔊</button></div>';
  }).join("");
  var actions = all ? '<div class="actions"><button class="speak small" data-speak="' + esc(all) + '">🔊 朗读全文</button></div>' : "";
  return '<details class="card"><summary><strong>转写稿</strong> <span class="muted small">版本 ' + d.transcript.version + " · " + d.word_count + " 词</span></summary>" +
    actions + (rows || '<div class="transcript">' + esc(body) + "</div>") + "</details>";
}

function errorsCard(d) {
  var errors = d.errors || [];
  var effective = errors.filter(function (e) { return e.effective; }).length;
  var cards = errors.map(function (e) {
    var certainty = e.certainty === "确定" ? '<span class="pill">确定</span>' : '<span class="pill warn">建议核对</span>';
    var action = e.teacher_action && e.teacher_action !== "pending" ? '<span class="pill muted">教师：' + esc(e.teacher_action) + "</span>" : "";
    return '<div class="error-card ' + (e.is_primary ? "" : "secondary") + '" data-error="' + esc(e.id) + '">' +
      '<div class="edit"><del>' + esc(e.original) + "</del> → <ins>" + esc(e.corrected) + "</ins></div>" +
      '<div class="error-meta">' + certainty + '<span class="pill muted">' + esc(e.type) + "</span>" +
      '<span class="pill muted">' + esc(e.error_code || e.pattern) + "</span>" + action + "</div>" +
      '<div class="small">' + esc(e.rule) + "</div>" +
      (e.review_reason ? '<div class="small muted">复核：' + esc(e.review_reason) + "</div>" : "") +
      '<div class="actions">' +
      '<button class="small speak" data-speak="' + esc(e.corrected) + '">🔊 读正确表达</button>' +
      '<button class="small" data-action="confirm" data-id="' + esc(e.id) + '">确认</button>' +
      '<button class="small" data-action="reject" data-id="' + esc(e.id) + '">AI 改错</button>' +
      '<button class="small" data-action="over_edit" data-id="' + esc(e.id) + '">改法过度</button>' +
      '<button class="small" data-action="correct" data-id="' + esc(e.id) + '">修正改法</button></div></div>';
  }).join("");
  return '<div class="card"><div class="card-head"><h2>错误清单</h2><span class="muted small">' + errors.length + " 条 · 计分 " + effective + " 条</span></div>" +
    (cards || '<p class="muted">没有发现错误。</p>') + "</div>";
}

function bindErrors(d) {
  Array.prototype.forEach.call(document.querySelectorAll("[data-action]"), function (button) {
    button.addEventListener("click", function () { reviewError(d, button.dataset.id, button.dataset.action); });
  });
}

async function reviewError(d, errorId, action) {
  var reason = action === "confirm" ? "教师确认" : "";
  var corrected = null;
  if (action === "reject") reason = prompt("说明为什么这是 AI 改错：", "AI 误判") || "";
  if (action === "over_edit") reason = prompt("说明为什么改法过度：", "改法过度") || "";
  if (action === "correct") {
    corrected = prompt("输入与原文片段对应的最小修改（只改出错部分）：", "");
    if (!corrected) return;
    reason = prompt("说明修正原因：", "教师修正改法") || "";
  }
  if (action !== "confirm" && !reason) return;
  try {
    var result = await api("/api/errors/" + errorId + "/review", {
      method: "POST", body: JSON.stringify({ action: action, reason: reason, corrected: corrected })
    });
    toast("已重算：" + fmt(result.total) + " 分");
    await openSubmission(d.submission.id);
  } catch (error) {
    toast(error.message, true);
  }
}

function checklistCard(d) {
  var rows = (d.checklist || []).map(function (item) {
    var value = JSON.stringify(item.value);
    var valid = item.valid ? "" : '<span class="pill danger">证据未通过</span>';
    var editor = item.valid ? "" : '<div class="actions"><button class="small" data-checklist="' + esc(item.key) + '">修正值</button></div>';
    return '<tr><td>' + esc(item.key) + "</td><td>" + valid + ' <span class="small">' + esc(value) + "</span>" + editor + "</td></tr>";
  }).join("");
  return '<div class="card"><div class="card-head"><h2>要素核对</h2><span class="muted small">证据必须是原文子串</span></div>' +
    '<table class="checklist"><thead><tr><th>字段</th><th>值与证据</th></tr></thead><tbody>' + (rows || '<tr><td colspan="2" class="muted">尚无要素结果</td></tr>') + "</tbody></table></div>";
}

function bindChecklist(d) {
  Array.prototype.forEach.call(document.querySelectorAll("[data-checklist]"), function (button) {
    button.addEventListener("click", async function () {
      var key = button.dataset.checklist;
      var item = d.checklist.filter(function (x) { return x.key === key; })[0];
      var raw = prompt("输入 " + key + " 的修正值（JSON）：", JSON.stringify(item.value === null ? true : item.value));
      if (raw === null) return;
      try {
        var value = JSON.parse(raw);
        var result = await api("/api/runs/" + d.run.id + "/checklist/" + key, {
          method: "POST", body: JSON.stringify({ value: value, evidence: item.evidence })
        });
        toast("已重算：" + fmt(result.total) + " 分");
        await openSubmission(d.submission.id);
      } catch (error) {
        toast(error.message, true);
      }
    });
  });
}

function scoreCard(d, scores, main) {
  var bars = [["语言", "language", 5], ["内容", "content", 3], ["结构", "structure", 2]].map(function (row) {
    var value = main[row[1]] || 0;
    return '<div class="score-row"><span>' + row[0] + "</span><strong>" + fmt(main[row[1]]) + " / " + row[2] + "</strong></div>" +
      '<div class="bar"><span style="width:' + Math.min(100, value / row[2] * 100) + '%"></span></div>';
  }).join("");
  var finalForm = d.run.status === "finalized" ? '<p class="muted small">已完成终审，分数与错误清单已锁定。</p>' :
    '<form id="final-form" class="final-form" style="margin-top:12px">' +
    '<label class="wide">改分原因<select name="reason"><option>量规理解不同</option><option>AI 漏错</option><option>AI 误判</option><option>转写问题</option><option>其他</option></select></label>' +
    '<label>语言<input name="language" type="number" step="0.5" min="0" max="5" value="' + (main.language === undefined ? "" : main.language) + '"></label>' +
    '<label>内容<input name="content" type="number" step="0.5" min="0" max="3" value="' + (main.content === undefined ? "" : main.content) + '"></label>' +
    '<label>结构<input name="structure" type="number" step="0.5" min="0" max="2" value="' + (main.structure === undefined ? "" : main.structure) + '"></label>' +
    '<label class="wide">备注<input name="note" placeholder="可选"></label>' +
    '<button class="primary" type="submit">完成终审</button></form>';
  return '<div class="card"><div class="card-head"><h2>分数</h2>' + (main.draft ? '<span class="pill warn">草稿</span>' : "") + "</div>" +
    '<div class="score-total">' + fmt(main.total) + ' <span class="muted" style="font-size:14px">/ 10</span></div>' +
    '<div class="muted small">AI ' + fmt(scores.ai && scores.ai.total) + " · 复核 " + fmt(scores.reviewed && scores.reviewed.total) + " · 终审 " + fmt(d.run.final_total) + "</div>" +
    bars + '<details class="trace"><summary>计分说明</summary><pre>' + esc(JSON.stringify(main.trace || [], null, 2)) + "</pre></details>" + finalForm + "</div>";
}

function bindFinalize(d) {
  var form = $("#final-form");
  if (!form) return;
  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    var data = new FormData(form);
    var body = { reason: data.get("reason"), note: data.get("note") || null };
    ["language", "content", "structure"].forEach(function (key) {
      if (data.get(key) !== "") body[key] = Number(data.get(key));
    });
    try {
      var result = await api("/api/submissions/" + d.submission.id + "/finalize", { method: "POST", body: JSON.stringify(body) });
      toast("终审完成：" + fmt(result.total) + " 分");
      await openSubmission(d.submission.id);
    } catch (error) {
      toast(error.message, true);
    }
  });
}

function commentsCard(d) {
  var c = d.comments || {};
  return '<div class="card"><div class="card-head"><h2>评语</h2>' + (c.fallback ? '<span class="pill warn">兜底文案</span>' : "") + "</div>" +
    "<p>" + esc(c.content_comment || "—") + "</p><p>" + esc(c.structure_comment || "—") + "</p>" +
    '<p class="muted">' + esc(c.language_summary || "—") + '</p><p><strong>' + esc(c.encouragement_de || "") + "</strong>" +
    (c.encouragement_de ? ' <button class="speak small" data-speak="' + esc(c.encouragement_de) + '">🔊 听德语鼓励</button>' : "") + "</p></div>";
}

async function showMetrics() {
  try {
    var data = await api("/api/metrics");
    var graded = data.graded || {};
    var errors = data.errors || {};
    var changeRate = graded.n ? (graded.changed || 0) / graded.n * 100 : 0;
    var rejection = errors.n ? (errors.rejected || 0) / errors.n * 100 : 0;
    var downgrade = errors.n ? (errors.downgraded || 0) / errors.n * 100 : 0;
    var quota = (data.quota || []).map(function (q) {
      return "<tr><td>" + esc(q.name) + "</td><td>" + fmt(q.available) + "</td><td>" + esc(q.reset_at) + "</td><td>" + (q.terms_ok ? "已核对" : "未核对") + "</td></tr>";
    }).join("");
    $("#content").innerHTML = '<div class="page-head"><div><h1>质量看板</h1><div class="meta muted small">演示数据只验证流程，不代表真实模型质量</div></div></div>' +
      '<div class="metric-grid">' +
      '<div class="metric"><div class="value">' + (graded.n || 0) + '</div><div class="label">已出分作文</div></div>' +
      '<div class="metric"><div class="value">' + fmt(graded.avg_final) + '</div><div class="label">平均终审分</div></div>' +
      '<div class="metric"><div class="value">' + changeRate.toFixed(1) + '%</div><div class="label">教师改分率</div></div>' +
      '<div class="metric"><div class="value">' + rejection.toFixed(1) + '%</div><div class="label">确定错误被驳回</div></div>' +
      '<div class="metric"><div class="value">' + downgrade.toFixed(1) + '%</div><div class="label">建议核对占比</div></div></div>' +
      '<div class="card" style="margin-top:14px"><div class="card-head"><h2>额度来源</h2></div>' +
      '<table class="plain"><thead><tr><th>来源</th><th>剩余</th><th>重置</th><th>条款</th></tr></thead><tbody>' + (quota || '<tr><td colspan="4" class="muted">无</td></tr>') + "</tbody></table></div>" +
      '<div class="card"><div class="card-head"><h2>作业状态</h2></div><pre class="trace">' + esc(JSON.stringify(data.statuses, null, 2)) + "</pre></div>";
  } catch (error) {
    toast(error.message, true);
  }
}

async function showProfile(studentId) {
  try {
    var data = await api("/api/profiles/" + studentId);
    var rows = (data.patterns || []).map(function (p) {
      return "<tr><td>" + esc(p.pattern) + "</td><td>" + esc(p.type) + "</td><td>" + fmt(p.rate) + "</td><td>" + esc(p.status) + "</td></tr>";
    }).join("");
    $("#content").innerHTML = '<div class="page-head"><div><h1>' + esc(data.student) + ' · 学生画像</h1><div class="meta muted small">只来自教师确认过的错误</div></div></div>' +
      '<div class="card"><table class="plain"><thead><tr><th>规则</th><th>类型</th><th>每百词</th><th>状态</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="4" class="muted">暂无画像</td></tr>') + "</tbody></table></div>";
  } catch (error) {
    toast(error.message, true);
  }
}

boot();
