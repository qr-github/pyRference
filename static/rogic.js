function check_urls(url_text){
    const urls = url_text.split('\n').map(urls => urls.trim()).filter(urls => urls.length > 0);
    if(urls.length === 0){
        return false;
    };
    return urls;
}

async function fetch_input(urls){
    const response = await fetch('/extract', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({urls: urls})
    });

    if (!response.ok){
        throw new Error("ページの取得に失敗しました。URLが正しいか確認するか、時間をおいて再度お試しください");
    };

    return await response.json();
}

let latex_data = {latex: "", ieee: ""};

const format_input = document.querySelectorAll('input[name="format"]');
const output_section = document.getElementById('output_section');

function render_result(data){
    textData = data.latex;

    const currentFormat = document.querySelector('input[name="format"]:checked');
    output_section.textContent = textData[currentFormat] || "";
}

format_input.forEach(input => {
    input.addEventListener('change', (e) => {
        const select_format = e.target.value;
        output_section.textContent = textData[select_format] || "";
    });
});

const toast = document.getElementById('toast');
function toast_text(text){
    toast.innerText = text;
}

function showToast(){
    toast.classList.remove('hidden');
    toast.classList.add('show');

    setTimeout(()=>{
        toast.classList.remove('show');
        toast.classList.add('hidden');
    }, 2000);
}

const extract_btn = document.getElementById('for_extract');
const input_form = document.querySelector('textarea[name="input_form"]');
const loading_section = document.getElementById('loading_section');
const errorText = document.getElementById('error_text');

extract_btn.addEventListener('click', async ()=>{
    const urls = check_urls(input_form.value);

    if(!urls){
        toast_text("urlを入力してください");
        showToast();
        return;
    };

    extract_btn.disabled = true;
    errorText.classList.add('hidden');
    loading_section.classList.remove('hidden');

    try{
        const data = await fetch_input(urls);
        render_result(data);

        loading_section.classList.add('hidden');
        toast_text("完了しました！");
        showToast();
    }catch(error){
        console.error("処理に失敗しました", error)
        loading_section.classList.add('hidden');
        errorText.textContent = error.message || "処理中に不明なエラーが発生しました";
        errorText.classList.remove('hidden');

        toast_text("error!");
        showToast();
    }finally{
        extract_btn.disabled = false;
    }
});

const copy_btn = document.getElementById('for_copy');

copy_btn.addEventListener('click', ()=>{
    const textToCopy = output_section.textContent;

    navigator.clipboard.writeText(textToCopy)
    .then(()=>{
        toast_text("copied!");
        showToast();
    });
});