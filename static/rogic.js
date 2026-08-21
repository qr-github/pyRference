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
        throw new Error(`Error!: ${response.status}`);
    };

    return await response.json();
}

function render_result(data){
    const output_section = document.getElementById('output_section');
    output_section.textContent = data.latex;
}

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

extract_btn.addEventListener('click', async ()=>{
    const urls = check_urls(input_form.value);

    if(!urls){
        toast_text("urlを入力してください");
        showToast();
        return;
    };

    try{
        const data = await fetch_input(urls);
        render_result(data);
    }catch(error){
        console.error("処理に失敗しました", error)
    };
});

const copy_btn = document.getElementById('for_copy');

copy_btn.addEventListener('click', ()=>{
    const output_section = document.getElementById('output_section');
    const textToCopy = output_section.textContent;

    navigator.clipboard.writeText(textToCopy)
    .then(()=>{
        toast_text("copied!");
        showToast();
    });
});