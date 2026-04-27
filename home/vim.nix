{ pkgs, ... }:
{
  programs.vim = {
    enable = true;
    defaultEditor = true;

    plugins = with pkgs.vimPlugins; [
      vim-hardtime            
      markdown-preview-nvim   
    ];

    extraConfig = ''
      let mapleader = " "

      " let g:hardtime_default_on = 1

      nnoremap <Space> <Nop>
      nnoremap <silent> <leader>gd <C-]>

      set nocompatible
      filetype on
      set relativenumber
      set number
      set scrolloff=8

      set hlsearch
      set incsearch

      set tabstop=4
      set softtabstop=4
      set shiftwidth=4
      set smartindent

      set t_Co=256
      colorscheme lunaperche
      highlight Normal ctermbg=NONE guibg=NONE

      set wildmenu
      syntax on

      " Auto-closing mappings
      inoremap [ []<Left>
      inoremap ( ()<Left>
      inoremap " ""<Left>
      inoremap ' '''<Left>
      inoremap {<CR> {<CR>}<Esc>O
      inoremap { {}<Left>

      set clipboard=unnamedplus
      set ignorecase
      set smartcase
      set tags=./tags;,tags;
    '';
  };
}
