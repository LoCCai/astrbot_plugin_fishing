// 用户物品管理JavaScript

document.addEventListener('DOMContentLoaded', function() {    
    // 初始化物品类型选择器
    initializeItemTypeSelector();
    
    // 绑定事件监听器
    bindEventListeners();

    // 恢复并监听标签状态
    restoreActiveTab();
    attachTabPersistence();
    initializeInventoryLists();
});

function initializeInventoryLists() {
    document.querySelectorAll('#inventoryTabsContent table').forEach(table => {
        const rows = Array.from(table.querySelectorAll('tbody tr[data-rarity]'));
        if (!rows.length) return;
        const toolbar = document.createElement('div');
        toolbar.className = 'd-flex flex-wrap gap-2 mb-3';
        toolbar.innerHTML = `
            <input type="search" class="form-control w-auto" placeholder="搜索名称或ID" aria-label="搜索物品">
            <select class="form-select w-auto" aria-label="筛选星级"><option value="">全部星级</option></select>
            <select class="form-select w-auto" aria-label="筛选装备状态">
                <option value="">全部状态</option><option value="available">可回收（未锁定、未装备）</option>
                <option value="locked">已锁定</option><option value="equipped">已装备</option>
            </select>`;
        const [raritySelect, stateSelect] = toolbar.querySelectorAll('select');
        const search = toolbar.querySelector('input');
        const rarities = [...new Set(rows.map(row => row.dataset.rarity))].sort((a, b) => Number(a) - Number(b));
        rarities.forEach(rarity => raritySelect.add(new Option(`${rarity}星`, rarity)));
        stateSelect.hidden = !rows.some(row => 'locked' in row.dataset);
        const pager = document.createElement('div');
        pager.className = 'd-flex flex-wrap align-items-center gap-2 mt-3';
        pager.innerHTML = '<button type="button" class="btn btn-outline-secondary btn-sm">上一页</button><span></span><button type="button" class="btn btn-outline-secondary btn-sm">下一页</button>';
        const [previous, next] = pager.querySelectorAll('button');
        const info = pager.querySelector('span');
        const wrapper = table.closest('.table-responsive');
        wrapper.before(toolbar);
        wrapper.after(pager);
        let page = 1;
        const pageSize = 25;
        function render() {
            const query = search.value.trim().toLocaleLowerCase();
            const matches = rows.filter(row =>
                (!query || row.textContent.toLocaleLowerCase().includes(query)) &&
                (!raritySelect.value || row.dataset.rarity === raritySelect.value) &&
                (!stateSelect.value || (stateSelect.value === 'available'
                    ? row.dataset.locked === '0' && row.dataset.equipped === '0'
                    : row.dataset[stateSelect.value] === '1')));
            const pages = Math.max(1, Math.ceil(matches.length / pageSize));
            page = Math.min(page, pages);
            rows.forEach(row => { row.hidden = true; });
            matches.slice((page - 1) * pageSize, page * pageSize).forEach(row => { row.hidden = false; });
            info.textContent = `第 ${page}/${pages} 页 · 筛选 ${matches.length}/${rows.length} 条 · 每页 ${pageSize} 条`;
            previous.disabled = page === 1;
            next.disabled = page === pages;
        }
        [search, raritySelect, stateSelect].forEach(control => control.addEventListener('input', () => { page = 1; render(); }));
        previous.addEventListener('click', () => { page--; render(); });
        next.addEventListener('click', () => { page++; render(); });
        render();
    });
}

function initializeItemTypeSelector() {
    const itemTypeSelect = document.getElementById('itemType');
    const itemIdSelect = document.getElementById('itemId');
    
    if (itemTypeSelect && itemIdSelect) {
        itemTypeSelect.addEventListener('change', function() {
            updateItemOptions(this.value);
        });
    }
}

function updateItemOptions(itemType) {
    const itemIdSelect = document.getElementById('itemId');
    const qualityLevelDiv = document.getElementById('qualityLevelDiv');
    
    if (!itemIdSelect) return;
    
    // 清空现有选项
    itemIdSelect.innerHTML = '<option value="">请选择物品</option>';
    
    // 显示/隐藏品质选择器
    if (qualityLevelDiv) {
        if (itemType === 'fish') {
            qualityLevelDiv.style.display = 'block';
        } else {
            qualityLevelDiv.style.display = 'none';
        }
    }
    
    let templates = [];
    switch (itemType) {
        case 'fish':
            templates = fishTemplates;
            break;
        case 'rod':
            templates = rodTemplates;
            break;
        case 'accessory':
            templates = accessoryTemplates;
            break;
        case 'bait':
            templates = baitTemplates;
            break;
        case 'item':
            templates = itemsTemplates;
            break;
        default:
            itemIdSelect.innerHTML = '<option value="">请先选择物品类型</option>';
            itemIdSelect.disabled = true;
            return;
    }
    
    // 添加选项
    templates.forEach(template => {
        const option = document.createElement('option');
        option.value = template[`${itemType}_id`];
        option.textContent = `${template.name} (${'★'.repeat(template.rarity)})`;
        itemIdSelect.appendChild(option);
    });
    
    itemIdSelect.disabled = false;
}

function bindEventListeners() {
    // 添加物品表单提交
    const addItemForm = document.getElementById('addItemForm');
    if (addItemForm) {
        addItemForm.addEventListener('submit', function(e) {
            e.preventDefault();
            addItem();
        });
    }
}

function getActiveTabTarget() {
    const active = document.querySelector('#inventoryTabs .nav-link.active');
    return active ? active.getAttribute('data-bs-target') : null;
}

function saveActiveTab() {
    try {
        const target = getActiveTabTarget();
        if (target) {
            localStorage.setItem(`ui_last_inv_tab_${userId}`, target);
        }
    } catch (e) {
        // 忽略存储错误
    }
}

function restoreActiveTab() {
    try {
        const saved = localStorage.getItem(`ui_last_inv_tab_${userId}`);
        if (!saved) return;
        const btn = document.querySelector(`#inventoryTabs button[data-bs-target="${saved}"]`);
        if (btn && window.bootstrap && bootstrap.Tab) {
            const tab = new bootstrap.Tab(btn);
            tab.show();
        } else if (btn) {
            // 兜底：模拟点击
            btn.click();
        }
    } catch (e) {
        // 忽略读取错误
    }
}

function attachTabPersistence() {
    const buttons = document.querySelectorAll('#inventoryTabs button[data-bs-toggle="tab"]');
    buttons.forEach(btn => {
        btn.addEventListener('shown.bs.tab', function() {
            try {
                const target = this.getAttribute('data-bs-target');
                if (target) localStorage.setItem(`ui_last_inv_tab_${userId}`, target);
            } catch (e) {}
        });
        // 兼容无 Bootstrap 事件的情况
        btn.addEventListener('click', saveActiveTab);
    });
}

function makeUpdateUrl(tmpl, instanceId) {
    return tmpl.replace('12345', String(instanceId));
}

async function editRodRefine(instanceId, currentLevel) {
    const input = prompt(`设置精炼等级 (1-10):`, String(currentLevel ?? 1));
    if (input === null) return;
    const level = parseInt(input);
    if (isNaN(level) || level < 1 || level > 10) {
        alert('请输入 1-10 的整数');
        return;
    }
    try {
        const resp = await fetch(makeUpdateUrl(updateRodInstanceUrlTemplate, instanceId), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refine_level: level })
        });
        const data = await resp.json();
        if (data.success) {
            showSuccess('精炼等级已更新');
            location.reload();
        } else {
            showError('更新失败：' + data.message);
        }
    } catch (e) {
        console.error(e);
        showError('请求失败');
    }
}

async function editRodDurability(instanceId, currentDurability) {
    const placeholder = (currentDurability === '' || currentDurability === null || typeof currentDurability === 'undefined') ? '无限(留空)' : String(currentDurability);
    const input = prompt(`设置耐久度（留空代表 ∞）:`, placeholder);
    if (input === null) return;
    const payload = {};
    if (input.trim() === '') {
        payload.durability = null;
    } else {
        const val = parseInt(input);
        if (isNaN(val) || val < 0) {
            alert('请输入非负整数，或留空表示无限');
            return;
        }
        payload.durability = val;
    }
    try {
        const resp = await fetch(makeUpdateUrl(updateRodInstanceUrlTemplate, instanceId), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await resp.json();
        if (data.success) {
            showSuccess('耐久度已更新');
            location.reload();
        } else {
            showError('更新失败：' + data.message);
        }
    } catch (e) {
        console.error(e);
        showError('请求失败');
    }
}

async function editAccessoryRefine(instanceId, currentLevel) {
    const input = prompt(`设置精炼等级 (1-10):`, String(currentLevel ?? 1));
    if (input === null) return;
    const level = parseInt(input);
    if (isNaN(level) || level < 1 || level > 10) {
        alert('请输入 1-10 的整数');
        return;
    }
    try {
        const resp = await fetch(makeUpdateUrl(updateAccessoryInstanceUrlTemplate, instanceId), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refine_level: level })
        });
        const data = await resp.json();
        if (data.success) {
            showSuccess('精炼等级已更新');
            location.reload();
        } else {
            showError('更新失败：' + data.message);
        }
    } catch (e) {
        console.error(e);
        showError('请求失败');
    }
}

async function addItem() {
    // 先保存当前标签
    saveActiveTab();
    const itemType = document.getElementById('itemType').value;
    const itemId = document.getElementById('itemId').value;
    const quantity = parseInt(document.getElementById('quantity').value);
    const qualityLevel = document.getElementById('qualityLevel') ? parseInt(document.getElementById('qualityLevel').value) : 0;
    
    if (!itemType || !itemId || !quantity) {
        alert('请填写完整信息');
        return;
    }
    
    try {
        const requestData = {
            item_type: itemType,
            item_id: parseInt(itemId),
            quantity: quantity
        };
        
        // 如果是鱼类，添加品质等级
        if (itemType === 'fish') {
            requestData.quality_level = qualityLevel;
        }
        
        const response = await fetch(addItemUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(requestData)
        });
        
        const data = await response.json();
        
        if (data.success) {
            alert(data.message);
            // 关闭模态框
            const modal = bootstrap.Modal.getInstance(document.getElementById('addItemModal'));
            if (modal) {
                modal.hide();
            }
            // 刷新页面（恢复到之前的标签）
            location.reload();
        } else {
            alert('添加失败：' + data.message);
        }
    } catch (error) {
        console.error('添加物品时发生错误:', error);
        alert('添加物品时发生错误');
    }
}

async function removeItem(itemType, itemId, maxQuantity) {
    // 先保存当前标签
    saveActiveTab();
    const quantity = prompt(`请输入要移除的数量 (最多 ${maxQuantity} 个):`, '1');
    
    if (quantity === null) return; // 用户取消
    
    const removeQuantity = parseInt(quantity);
    if (isNaN(removeQuantity) || removeQuantity <= 0) {
        alert('请输入有效的数量');
        return;
    }
    
    if (removeQuantity > maxQuantity) {
        alert(`数量不能超过 ${maxQuantity}`);
        return;
    }
    
    if (!confirm(`确定要移除 ${removeQuantity} 个物品吗？`)) {
        return;
    }
    
    try {
        const response = await fetch(removeItemUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                item_type: itemType,
                item_id: itemId,
                quantity: removeQuantity
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            alert(data.message);
            // 刷新页面（恢复到之前的标签）
            location.reload();
        } else {
            alert('移除失败：' + data.message);
        }
    } catch (error) {
        console.error('移除物品时发生错误:', error);
        alert('移除物品时发生错误');
    }
}

// 工具函数：格式化数字
function formatNumber(num) {
    return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

// 工具函数：显示加载状态
function showLoading(element, text = '处理中...') {
    const originalText = element.textContent;
    element.textContent = text;
    element.disabled = true;
    return function hideLoading() {
        element.textContent = originalText;
        element.disabled = false;
    };
}

// 工具函数：显示成功消息
function showSuccess(message) {
    // 创建临时提示框
    const alertDiv = document.createElement('div');
    alertDiv.className = 'alert alert-success alert-dismissible fade show position-fixed';
    alertDiv.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
    alertDiv.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    document.body.appendChild(alertDiv);
    
    // 3秒后自动移除
    setTimeout(() => {
        if (alertDiv.parentNode) {
            alertDiv.parentNode.removeChild(alertDiv);
        }
    }, 3000);
}

// 工具函数：显示错误消息
function showError(message) {
    // 创建临时提示框
    const alertDiv = document.createElement('div');
    alertDiv.className = 'alert alert-danger alert-dismissible fade show position-fixed';
    alertDiv.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
    alertDiv.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    document.body.appendChild(alertDiv);
    
    // 5秒后自动移除
    setTimeout(() => {
        if (alertDiv.parentNode) {
            alertDiv.parentNode.removeChild(alertDiv);
        }
    }, 5000);
}
