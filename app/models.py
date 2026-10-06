from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FlexibleModel(BaseModel):
    """Accept extra fields so platform payload evolution does not break the agent."""

    model_config = ConfigDict(extra="allow")


class ThongTinToKhai(FlexibleModel):
    nhom_loai_hinh: str = ""
    so_to_khai: str = ""
    stt: str = ""
    so_to_khai_dau_tien: str = ""
    ma_loai_hinh: str = ""
    co_quan_hai_quan: str = ""
    ten_co_quan_hai_quan: str = ""
    ma_bo_phan_xu_ly_to_khai: str = ""
    ma_hieu_phuong_thuc_van_chuyen: str = ""
    ngay_khai_bao_du_kien: str = ""
    ngay_dang_ky: str = ""


class NguoiXuatKhau(FlexibleModel):
    ma: str = ""
    ten: str = ""
    ma_buu_chinh: str = ""
    dia_chi: str = ""
    dien_thoai: str = ""


class NguoiNhapKhau(FlexibleModel):
    ma: str = ""
    ten: str = ""
    ma_buu_chinh: str = ""
    dia_chi: str = ""
    ma_nuoc: str = ""
    ten_nuoc: str = ""
    ma_nguoi_khai_hai_quan: str = ""


class VanDon(FlexibleModel):
    so_van_don: str = ""
    so_luong_kien: str = ""
    tong_trong_luong_hang_gross: str = ""
    dia_diem_luu_kho_ma: str = ""
    dia_diem_luu_kho_ten: str = ""
    dia_diem_luu_kho_hang_cho_thong_quan_du_kien: str = ""
    dia_diem_nhan_hang_ma: str = ""
    dia_diem_nhan_hang_ten: str = ""
    dia_diem_nhan_hang_cuoi_cung: str = ""
    dia_diem_xep_hang_ma: str = ""
    dia_diem_xep_hang_ten: str = ""
    dia_diem_xep_hang: str = ""
    phuong_tien_van_chuyen_ma: str = ""
    phuong_tien_van_chuyen_ten: str = ""
    phuong_tien_van_chuyen_du_kien: str = ""
    ngay_hang_di_du_kien: str = ""
    ky_hieu_va_so_hieu: str = ""


class HopDong(FlexibleModel):
    so_hop_dong: str = ""
    ngay_hop_dong: str = ""
    ngay_het_han: str = ""


class HoaDon(FlexibleModel):
    phan_loai_hinh_thuc_hoa_don: str = ""
    so_hoa_don: str = ""
    ngay_phat_hanh: str = ""
    phuong_thuc_thanh_toan: str = ""
    ma_phan_loai_gia_hoa_don: str = ""
    dieu_kien_gia_hoa_don: str = ""
    tong_tri_gia_hoa_don: str = ""
    tri_gia_tinh_thue: str = ""
    ma_dong_tien_cua_hoa_don: str = ""
    ma_dong_tien_tri_gia_tinh_thue: str = ""
    tong_he_so_phan_bo_tri_gia_tinh_thue: str = ""


class VanChuyen(FlexibleModel):
    ngay_khoi_hanh_van_chuyen: str = ""
    dia_diem_dich_cho_van_chuyen_bao_thue_ma: str = ""
    dia_diem_dich_cho_van_chuyen_bao_thue_ten: str = ""
    dia_diem_dich_cho_van_chuyen_bao_thue: str = ""
    ngay_den_ngay_khoi_hanh_bao_thue: str = ""


class ThongTinKhac(FlexibleModel):
    phan_ghi_chu: str = ""
    so_quan_ly_cua_noi_bo_doanh_nghiep: str = ""
    so_quan_ly_nguoi_su_dung: str = ""
    tong_so_trang_cua_to_khai: str = ""
    tong_so_dong_hang_cua_to_khai: Any = ""


class ThongTinChung(FlexibleModel):
    tieu_de: str = ""
    thong_tin_to_khai: ThongTinToKhai = Field(default_factory=ThongTinToKhai)
    nguoi_xuat_khau: NguoiXuatKhau = Field(default_factory=NguoiXuatKhau)
    nguoi_uy_thac_xuat_khau: dict[str, Any] = Field(default_factory=dict)
    nguoi_nhap_khau: NguoiNhapKhau = Field(default_factory=NguoiNhapKhau)
    van_don: VanDon = Field(default_factory=VanDon)
    hop_dong: HopDong = Field(default_factory=HopDong)
    hoa_don: HoaDon = Field(default_factory=HoaDon)
    van_chuyen: VanChuyen = Field(default_factory=VanChuyen)
    thong_tin_khac: ThongTinKhac = Field(default_factory=ThongTinKhac)


class ThongTinContainer(FlexibleModel):
    tieu_de: str = ""
    dia_diem_xep_hang_len_xe_cho_hang: dict[str, Any] = Field(default_factory=dict)
    so_container_1_50: str = ""


class HangHoaItem(FlexibleModel):
    stt: Any = 0
    ma_hang: str = ""
    ten_hang: str = ""
    ma_hs: str = ""
    xuat_xu: str = ""
    so_luong_1: Any = ""
    don_vi_tinh_1: str = ""
    so_luong_2: Any = ""
    don_vi_tinh_2: str = ""
    don_gia: Any = ""
    ma_nguyen_te_don_gia: str = ""
    don_vi_tinh_don_gia: str = ""
    tri_gia_hoa_don: Any = ""
    tri_gia_tinh_thue: Any = ""
    ma_nguyen_te_tri_gia_tinh_thue: str = ""


class DanhSachHang(FlexibleModel):
    tieu_de: str = ""
    hang_hoa: list[HangHoaItem] = Field(default_factory=list)
    tong_cong: dict[str, Any] = Field(default_factory=dict)


class EcusJobRequest(FlexibleModel):
    """Body n8n sends to POST /jobs (one declaration sheet)."""

    model_config = ConfigDict(extra="allow")

    sendId: str = ""
    jobId: str = ""
    jobIndex: int = 1
    jobTotal: int = 1
    action: str = "register_declaration"
    userId: str = ""
    userName: str = ""
    department: str = ""
    request_ids: list[str] = Field(default_factory=list)
    thong_tin_chung: ThongTinChung = Field(default_factory=ThongTinChung)
    thong_tin_container: ThongTinContainer = Field(default_factory=ThongTinContainer)
    danh_sach_hang: DanhSachHang = Field(default_factory=DanhSachHang)


class EcusJobResponse(BaseModel):
    success: bool
    message: str
    so_to_khai: str = ""
    jobId: str = ""
    sendId: str = ""
    status: str = ""
    screenshot: str | None = None
    hang_hoa_requested: int = 0
    hang_hoa_filled: int = 0
    hang_hoa_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    backend: str = ""
    position: int = 0
