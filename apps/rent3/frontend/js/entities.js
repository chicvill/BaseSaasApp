/**
 * apps/rent3/frontend/js/entities.js — 8대 표준 데이터 객체 관리 및 16단계 이벤트 라이프사이클 엔진 모듈
 */

let currentEntityType = 'admin';
let cachedEntities = [];
let entitySearchDebounceTimer = null;

const ENTITY_CONFIG = {
  admin: {
    name: 'Admin (관리인)',
    badgeId: 'entCountAdmin',
    idField: 'id',
    columns: ['ID', '성명', '전화번호', '계좌번호', '사업자 번호', 'email', '조치'],
    fields: [
      { key: 'name', label: '성명', type: 'text', required: true, default: '' },
      { key: 'phone', label: '전화번호', type: 'text', required: true, default: '010-' },
      { key: 'account_number', label: '계좌번호', type: 'text', default: '' },
      { key: 'business_number', label: '사업자 번호', type: 'text', default: '' },
      { key: 'email', label: 'email', type: 'email', default: '' }
    ]
  },
  lessor: {
    name: 'Lessor (임대인)',
    badgeId: 'entCountLessor',
    idField: 'id_lessor',
    columns: ['임대인ID', '성명', '전화번호', '계좌번호', '앱 사용료', '가입일자', '사업자 번호', 'email', '조치'],
    fields: [
      { key: 'name', label: '성명', type: 'text', required: true, default: '' },
      { key: 'phone', label: '전화번호', type: 'text', required: true, default: '010-' },
      { key: 'account_number', label: '계좌번호', type: 'text', default: '' },
      { key: 'app_fee', label: '앱 사용료 (원)', type: 'number', default: 33000 },
      { key: 'joined_at', label: '가입일자', type: 'date', default: '2026-10-28' },
      { key: 'business_number', label: '사업자 번호', type: 'text', default: '' },
      { key: 'email', label: 'email', type: 'email', default: '' },
      { key: 'branch', label: '소속 지점 (main/sunho/sinbi)', type: 'text', default: 'main' }
    ]
  },
  tenant: {
    name: 'Tenant (임차인)',
    badgeId: 'entCountTenant',
    idField: 'id_tenant',
    columns: ['임차인ID', '성명', '전화번호', '계좌번호', '차량번호', '도어락 비번', '사업자 번호', 'email', '조치'],
    fields: [
      { key: 'name', label: '성명', type: 'text', required: true, default: '' },
      { key: 'phone', label: '전화번호', type: 'text', required: true, default: '010-' },
      { key: 'account_number', label: '계좌번호', type: 'text', default: '' },
      { key: 'car_number', label: '차량번호', type: 'text', default: '' },
      { key: 'doorlock_pwd', label: '도어락 비번', type: 'text', default: '1234*' },
      { key: 'business_number', label: '사업자 번호 (세액공제용)', type: 'text', default: '' },
      { key: 'email', label: 'email', type: 'email', default: '' }
    ]
  },
  building: {
    name: 'Building (건물)',
    badgeId: 'entCountBuilding',
    idField: 'id_building',
    columns: ['건물ID', '건물명', '주소', '소유 임대인ID', '소속 지점', '총 호실', '조치'],
    fields: [
      { key: 'building_name', label: '건물명', type: 'text', required: true, default: '' },
      { key: 'address', label: '주소', type: 'text', required: true, default: '' },
      { key: 'id_lessor', label: '소유 임대인 ID (id_lessor)', type: 'text', default: '' },
      { key: 'branch', label: '소속 지점 키', type: 'text', default: 'main' },
      { key: 'total_rooms', label: '총 호실 수', type: 'number', default: 20 }
    ]
  },
  room: {
    name: 'Room (임대상세)',
    badgeId: 'entCountRoom',
    idField: 'id_room',
    columns: ['호실ID', '호실명', '건물ID', '임차인ID', '등기부확인', '상태 설명', '보증금/월세', '조치'],
    fields: [
      { key: 'name', label: '호실명 (예: 201호)', type: 'text', required: true, default: '' },
      { key: 'id_building', label: '건물 ID (id_building)', type: 'text', default: '' },
      { key: 'id_tenant', label: '입주 임차인 ID (공실 시 공백)', type: 'text', default: '' },
      { key: 'status_description', label: '상태 설명', type: 'text', default: '남향 풀옵션, 신규 도배장판 완료' },
      { key: 'deposit', label: '보증금 (원)', type: 'number', default: 5000000 },
      { key: 'monthly_rent', label: '임차료 (원)', type: 'number', default: 500000 },
      { key: 'maintenance_fee', label: '관리비 (원)', type: 'number', default: 50000 }
    ]
  },
  contract: {
    name: 'Contract (계약)',
    badgeId: 'entCountContract',
    idField: 'id_contract',
    columns: ['계약ID', '호실ID', '임차인ID', '계약일', '계약금', '전입일', '임차기간', '보증금/임차료', '구분', '조치'],
    fields: [
      { key: 'id_room', label: '호실 ID (id_room)', type: 'text', required: true, default: '' },
      { key: 'id_tenant', label: '임차인 ID (id_tenant)', type: 'text', required: true, default: '' },
      { key: 'contract_date', label: '계약일', type: 'date', default: '2026-10-28' },
      { key: 'contract_deposit', label: '계약금 (원)', type: 'number', default: 300000 },
      { key: 'move_in_date', label: '전입일', type: 'date', default: '2026-11-01' },
      { key: 'lease_term', label: '임차기간', type: 'text', default: '12개월' },
      { key: 'deposit', label: '보증금 (원)', type: 'number', default: 5000000 },
      { key: 'monthly_rent', label: '임차료 (원)', type: 'number', default: 500000 },
      { key: 'maintenance_fee', label: '관리비 (원)', type: 'number', default: 50000 },
      { key: 'rent_type', label: '전세/월세', type: 'text', default: '월세' },
      { key: 'payment_timing', label: '선불/후불', type: 'text', default: '후불' }
    ]
  },
  pay: {
    name: 'Pay (수납)',
    badgeId: 'entCountPay',
    idField: 'id_pay',
    columns: ['수납ID', '호실ID', '적용년월', '납부일', '임차료', '관리비', '총 납부액', '영수증번호', '조치'],
    fields: [
      { key: 'id_room', label: '호실 ID (id_room)', type: 'text', required: true, default: '' },
      { key: 'billing_month', label: '적용년월 (예: 2026.10)', type: 'text', required: true, default: '2026.10' },
      { key: 'paid_date', label: '납부일', type: 'date', default: '2026-10-28' },
      { key: 'monthly_rent', label: '임차료 (원)', type: 'number', default: 500000 },
      { key: 'maintenance_fee', label: '관리비 (원)', type: 'number', default: 50000 },
      { key: 'vat', label: '부가세 (원)', type: 'number', default: 0 }
    ]
  },
  outlay: {
    name: 'Outlay (지출)',
    badgeId: 'entCountOutlay',
    idField: 'id_outlay',
    columns: ['지출ID', '호실/공용', '적요', '일자', '금액', '분류', '공급처', '조치'],
    fields: [
      { key: 'id_room', label: '호실 ID 또는 공용(COMMON)', type: 'text', required: true, default: 'COMMON' },
      { key: 'summary', label: '적요 (청소비, 차량유지비, 공동전기 등)', type: 'text', required: true, default: '' },
      { key: 'expense_date', label: '지출 일자', type: 'date', default: '2026-10-28' },
      { key: 'amount', label: '금액 (원)', type: 'number', required: true, default: 100000 },
      { key: 'category', label: '분류 (cleaning/elevator/utility_common/repair_maint 등)', type: 'text', default: 'cleaning' },
      { key: 'vendor_name', label: '공급업체/거래처', type: 'text', default: '' }
    ]
  }
};

async function loadEntitiesDashboard() {
  try {
    const res = await fetch('/api/rent3/entities/summary/dashboard');
    const data = await res.json();
    if (data.counts) {
      document.getElementById('entCountAdmin').innerText = data.counts.admins || 0;
      document.getElementById('entCountLessor').innerText = data.counts.lessors || 0;
      document.getElementById('entCountTenant').innerText = data.counts.tenants || 0;
      document.getElementById('entCountBuilding').innerText = data.counts.buildings || 0;
      document.getElementById('entCountRoom').innerText = data.counts.rooms || 0;
      document.getElementById('entCountContract').innerText = data.counts.contracts || 0;
      document.getElementById('entCountPay').innerText = data.counts.pays || 0;
      document.getElementById('entCountOutlay').innerText = data.counts.outlays || 0;
    }
    await loadEntitiesTable();
  } catch (err) {
    console.error('loadEntitiesDashboard error:', err);
  }
}

function switchEntitySubTab(type) {
  currentEntityType = type;
  document.querySelectorAll('#entityPillsContainer .entity-pill').forEach(btn => {
    btn.classList.remove('active');
    if (btn.getAttribute('onclick')?.includes(type)) {
      btn.classList.add('active');
    }
  });
  loadEntitiesTable();
}

async function loadEntitiesTable(query = '') {
  const cfg = ENTITY_CONFIG[currentEntityType];
  const thead = document.getElementById('entityTableHead');
  const tbody = document.getElementById('entityTableBody');
  
  if (!thead || !tbody) return;
  thead.innerHTML = `<tr>${cfg.columns.map(c => `<th>${c}</th>`).join('')}</tr>`;
  tbody.innerHTML = `<tr><td colspan="${cfg.columns.length}" style="text-align:center; padding:30px; color:var(--text-muted);">불러오는 중...</td></tr>`;

  try {
    const url = `/api/rent3/entities/${currentEntityType}?query=${encodeURIComponent(query)}`;
    const res = await fetch(url);
    const data = await res.json();
    cachedEntities = data.items || [];
    renderEntitiesTable(cachedEntities);
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="${cfg.columns.length}" style="text-align:center; padding:30px; color:#ef4444;">조회 실패: ${err.message}</td></tr>`;
  }
}

function renderEntitiesTable(items) {
  const cfg = ENTITY_CONFIG[currentEntityType];
  const tbody = document.getElementById('entityTableBody');
  if (!tbody) return;
  
  if (!items || items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="${cfg.columns.length}" style="text-align:center; padding:30px; color:var(--text-muted);">등록된 ${cfg.name} 객체가 없습니다.</td></tr>`;
    return;
  }

  tbody.innerHTML = items.map(item => {
    const customId = item[cfg.idField] || item.id;
    let cells = '';

    if (currentEntityType === 'admin') {
      cells = `
        <td><code>${item.id}</code></td>
        <td><strong>${item.name || ''}</strong></td>
        <td>${item.phone || ''}</td>
        <td>${item.account_number || ''}</td>
        <td>${item.business_number || ''}</td>
        <td>${item.email || ''}</td>
      `;
    } else if (currentEntityType === 'lessor') {
      cells = `
        <td><code>${item.id_lessor}</code></td>
        <td><strong>${item.name || ''}</strong></td>
        <td>${item.phone || ''}</td>
        <td>${item.account_number || ''}</td>
        <td style="color:#38bdf8; font-weight:700;">${(item.app_fee || 0).toLocaleString()}원</td>
        <td>${item.joined_at || ''}</td>
        <td>${item.business_number || ''}</td>
        <td>${item.email || ''}</td>
      `;
    } else if (currentEntityType === 'tenant') {
      cells = `
        <td><code>${item.id_tenant}</code></td>
        <td><strong>${item.name || ''}</strong></td>
        <td>${item.phone || ''}</td>
        <td>${item.account_number || ''}</td>
        <td>${item.car_number || '-'}</td>
        <td><code style="color:#f59e0b;">${item.doorlock_pwd || ''}</code></td>
        <td>${item.business_number || '-'}</td>
        <td>${item.email || ''}</td>
      `;
    } else if (currentEntityType === 'building') {
      cells = `
        <td><code>${item.id_building}</code></td>
        <td><strong>${item.building_name || ''}</strong></td>
        <td>${item.address || ''}</td>
        <td><code>${item.id_lessor || '-'}</code></td>
        <td><span class="badge badge-info">${item.branch || 'main'}</span></td>
        <td>${item.total_rooms || 0}실</td>
      `;
    } else if (currentEntityType === 'room') {
      const regChecked = item.registry_checked 
        ? `<span style="color:#10b981; font-weight:700;">✅ 확인완료</span>` 
        : `<span style="color:#f87171;">미확인</span>`;
      cells = `
        <td><code>${item.id_room}</code></td>
        <td><strong>${item.name || ''}</strong></td>
        <td><code>${item.id_building || '-'}</code></td>
        <td>${item.id_tenant ? `<code>${item.id_tenant}</code>` : '<span style="color:#38bdf8;">공실</span>'}</td>
        <td>${regChecked}</td>
        <td style="font-size:12px; color:var(--text-muted);">${item.status_description || ''}</td>
        <td><strong>${((item.deposit||0)/10000).toLocaleString()}만 / ${(item.monthly_rent||0).toLocaleString()}원</strong></td>
      `;
    } else if (currentEntityType === 'contract') {
      cells = `
        <td><code>${item.id_contract}</code></td>
        <td><code>${item.id_room || ''}</code></td>
        <td><code>${item.id_tenant || ''}</code></td>
        <td>${item.contract_date || ''}</td>
        <td style="color:#10b981; font-weight:700;">${(item.contract_deposit || 0).toLocaleString()}원</td>
        <td>${item.move_in_date || ''}</td>
        <td>${item.lease_term || ''}</td>
        <td><strong>${((item.deposit||0)/10000).toLocaleString()}만 / ${(item.monthly_rent||0).toLocaleString()}원</strong></td>
        <td><span class="badge badge-info">${item.rent_type || '월세'} (${item.payment_timing || '후불'})</span></td>
      `;
    } else if (currentEntityType === 'pay') {
      cells = `
        <td><code>${item.id_pay}</code></td>
        <td><code>${item.id_room || ''}</code></td>
        <td><strong>${item.billing_month || ''}</strong></td>
        <td>${item.paid_date || ''}</td>
        <td>${(item.monthly_rent || 0).toLocaleString()}원</td>
        <td>${(item.maintenance_fee || 0).toLocaleString()}원</td>
        <td style="color:#10b981; font-weight:800;">${(item.total_amount || 0).toLocaleString()}원</td>
        <td><code style="font-size:11px;">${item.receipt_number || '-'}</code></td>
      `;
    } else if (currentEntityType === 'outlay') {
      cells = `
        <td><code>${item.id_outlay}</code></td>
        <td><code>${item.id_room || 'COMMON'}</code></td>
        <td><strong>${item.summary || item.summary_desc || ''}</strong></td>
        <td>${item.expense_date || ''}</td>
        <td style="color:#f87171; font-weight:800;">-${(item.amount || 0).toLocaleString()}원</td>
        <td><span class="badge badge-warning">${item.category || ''}</span></td>
        <td>${item.vendor_name || '-'}</td>
      `;
    }

    return `
      <tr>
        ${cells}
        <td>
          <div style="display:flex; gap:6px;">
            <button class="btn btn-secondary" style="padding:3px 8px; font-size:11px;" onclick="openEditEntityModal('${currentEntityType}', '${customId}')">✏️ 편집</button>
            <button class="btn btn-secondary" style="padding:3px 8px; font-size:11px; border-color:rgba(239,68,68,0.4); color:#f87171;" onclick="deleteEntityRecord('${currentEntityType}', '${customId}')">🗑️ 삭제</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

function onEntitySearchInput() {
  if (entitySearchDebounceTimer) clearTimeout(entitySearchDebounceTimer);
  entitySearchDebounceTimer = setTimeout(() => {
    const q = document.getElementById('entitySearchKeyword')?.value || '';
    loadEntitiesTable(q);
  }, 250);
}

function executeEntitySearch() {
  const q = document.getElementById('entitySearchKeyword')?.value || '';
  loadEntitiesTable(q);
}

function openNewEntityModal() {
  const cfg = ENTITY_CONFIG[currentEntityType];
  document.getElementById('entFormMode').value = 'create';
  document.getElementById('entFormType').value = currentEntityType;
  document.getElementById('entFormId').value = '';
  document.getElementById('entityModalTitle').innerText = `➕ 신규 ${cfg.name} 등록`;
  document.getElementById('entityModalSub').innerText = '객체의 필드 값을 입력하여 데이터베이스에 저장합니다.';
  document.getElementById('btnEntityModalSubmit').innerText = '저장하기';

  renderFormFields(cfg.fields, {});
  document.getElementById('entityModal').style.display = 'flex';
}

function openEditEntityModal(type, customId) {
  const cfg = ENTITY_CONFIG[type];
  const item = cachedEntities.find(i => (i[cfg.idField] === customId || i.id === customId));
  if (!item) {
    alert('객체 정보를 찾을 수 없습니다.');
    return;
  }

  document.getElementById('entFormMode').value = 'update';
  document.getElementById('entFormType').value = type;
  document.getElementById('entFormId').value = customId;
  document.getElementById('entityModalTitle').innerText = `✏️ ${cfg.name} 편집 (${customId})`;
  document.getElementById('entityModalSub').innerText = '기존 객체 필드 값을 수정합니다.';
  document.getElementById('btnEntityModalSubmit').innerText = '수정 완료';

  renderFormFields(cfg.fields, item);
  document.getElementById('entityModal').style.display = 'flex';
}

function renderFormFields(fields, data) {
  const container = document.getElementById('entityFormFields');
  if (!container) return;
  container.innerHTML = fields.map(f => {
    let val = data[f.key];
    if (val === undefined || val === null) {
      if (f.key === 'summary' && data.summary_desc) val = data.summary_desc;
      else val = f.default;
    }

    return `
      <div style="display:flex; flex-direction:column; gap:4px;">
        <label style="font-size:12.5px; font-weight:700; color:#cbd5e1;">${f.label} ${f.required ? '<span style="color:#f87171;">*</span>' : ''}</label>
        <input type="${f.type}" id="field_${f.key}" value="${val}" style="background:#0b1120; border:1px solid var(--card-border); border-radius:8px; padding:8px 12px; color:#fff; font-size:13px;" ${f.required ? 'required' : ''}>
      </div>
    `;
  }).join('');
}

function closeEntityModal() {
  document.getElementById('entityModal').style.display = 'none';
}

async function submitEntityForm() {
  const mode = document.getElementById('entFormMode').value;
  const type = document.getElementById('entFormType').value;
  const customId = document.getElementById('entFormId').value;
  const cfg = ENTITY_CONFIG[type];

  const payload = {};
  cfg.fields.forEach(f => {
    const el = document.getElementById(`field_${f.key}`);
    if (el) {
      if (f.type === 'number') {
        payload[f.key] = parseInt(el.value) || 0;
      } else {
        payload[f.key] = el.value;
      }
    }
  });

  try {
    let url = `/api/rent3/entities/${type}`;
    let method = 'POST';
    if (mode === 'update') {
      url = `/api/rent3/entities/${type}/${customId}`;
      method = 'PUT';
    }

    const res = await fetch(url, {
      method: method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const result = await res.json();
    if (!result.success) {
      alert('저장 실패: ' + (result.detail || '알 수 없는 오류'));
      return;
    }

    closeEntityModal();
    if (window.Bass?.updateAppAlert) {
      Bass.updateAppAlert(`✅ ${cfg.name} 객체가 성공적으로 저장되었습니다.`, 'success');
    }
    loadEntitiesDashboard();
  } catch (err) {
    alert('통신 오류: ' + err.message);
  }
}

async function deleteEntityRecord(type, customId) {
  const cfg = ENTITY_CONFIG[type];
  if (!confirm(`정말로 ${cfg.name} 객체(${customId})를 삭제하시겠습니까?`)) {
    return;
  }

  try {
    const res = await fetch(`/api/rent3/entities/${type}/${customId}`, { method: 'DELETE' });
    const result = await res.json();
    if (!result.success) {
      alert('삭제 실패: ' + (result.detail || '알 수 없는 오류'));
      return;
    }
    if (window.Bass?.updateAppAlert) {
      Bass.updateAppAlert(`🗑️ ${cfg.name} 객체(${customId})가 삭제되었습니다.`, 'warning');
    }
    loadEntitiesDashboard();
  } catch (err) {
    alert('삭제 오류: ' + err.message);
  }
}

// ── 16단계 워크플로우 시뮬레이터 함수 ──
function openWorkflowModal() {
  document.getElementById('workflowModal').style.display = 'flex';
}

function closeWorkflowModal() {
  document.getElementById('workflowModal').style.display = 'none';
}

async function runWorkflowAction(actionName) {
  const logBox = document.getElementById('workflowConsoleLog');
  if (!logBox) return;
  logBox.innerHTML += `\n> [${new Date().toLocaleTimeString()}] 액션 '${actionName}' 실행 요청 중...`;
  logBox.scrollTop = logBox.scrollHeight;

  try {
    const res = await fetch(`/api/rent3/entities/workflow/${actionName}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    });
    const result = await res.json();
    if (result.success) {
      logBox.innerHTML += `\n✅ 성공: ${JSON.stringify(result, null, 2)}`;
      loadEntitiesDashboard();
    } else {
      logBox.innerHTML += `\n❌ 실패: ${JSON.stringify(result)}`;
    }
  } catch (err) {
    logBox.innerHTML += `\n❌ 네트워크 에러: ${err.message}`;
  }
  logBox.scrollTop = logBox.scrollHeight;
}
