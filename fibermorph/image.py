print("Executing image.py")
# %% Import libraries
import argparse
import datetime
import os
import pathlib
import shutil
import sys
import timeit
import warnings
from datetime import datetime
from functools import wraps
from timeit import default_timer as timer

# import cv2
import numpy as np
import pandas as pd
import rawpy
import scipy
import skimage
import contextlib
import joblib
import skimage.exposure
import skimage.measure
import skimage.morphology
from PIL import Image
from joblib import Parallel, delayed
from matplotlib import pyplot as plt
from scipy import ndimage
from scipy.spatial import distance as dist
from skimage import filters, io
from skimage.filters import threshold_minimum
from skimage.segmentation import clear_border
from skimage.util import invert
from tqdm import tqdm

#%%
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from common import blockPrint, make_subdirectory, tqdm_joblib
from utils import convert

# Grab version from _version.py in the fibermorph directory
dir = os.path.dirname(__file__)
version_py = os.path.join(dir, "_version.py")
exec(open(version_py).read())

@blockPrint
def list_images(directory):
    """Generates a list of all .tif and/or .tiff files in a directory.

    Parameters
    ----------
    directory : str
        The directory in which the function will recursively search for .tif and .tiff files.

    Returns
    -------
    list
        A list of pathlib objects with the paths to the image files.

    """
    exts = [".tif", ".tiff"]
    mainpath = pathlib.Path(directory)
    file_list = [p for p in pathlib.Path(mainpath).rglob('*') if p.suffix in exts]
    
    list.sort(file_list)  # sort the files
    # print(len(file_list))  # printed the sorted files
    
    return file_list

# @timing
def raw_to_gray(imgfile, output_directory):
    """Function to convert raw image file into tiff file.

    Parameters
    ----------
    imgfile : str
        Path to raw image file.
    output_directory : str
        String with the path where the converted images should be created.

    Returns
    -------
    pathlib object
        A pathlib object with the path to the converted image file.

    """
    
    imgfile = os.path.abspath(imgfile)
    output_directory = output_directory
    basename = os.path.basename(imgfile)
    name = os.path.splitext(basename)[0] + ".tiff"
    output_name = pathlib.Path(output_directory).joinpath(name)
    # print("\n\n")
    # print(name)
    
    try:
        with rawpy.imread(imgfile) as raw:
            rgb = raw.postprocess(use_auto_wb=True)
            im = Image.fromarray(rgb).convert('LA')
            im.save(str(output_name))
    except:
        # print("\nSomething is wrong with {}\n".format(str(imgfile)))
        pass
    
    # print('{} has been successfully converted to a grayscale tiff.\n Path is {}\n'.format(name, output_name))
    
    return output_name


def section_props(props, im_name, resolution, minpixel, maxpixel, im_center):
    props_df = [
        [region.label, region.centroid, scipy.spatial.distance.euclidean(im_center, region.centroid), region.filled_area, region.minor_axis_length, region.major_axis_length, region.eccentricity, region.filled_image, region.bbox]
        for region
        in props if region.minor_axis_length >= minpixel and region.major_axis_length <= maxpixel]
    props_df = pd.DataFrame(props_df, columns=['label', 'centroid', 'distance', 'area', 'min', 'max', 'eccentricity', 'image', 'bbox'])
    
    section_id = props_df['distance'].astype(float).idxmin()
    # print(section_id)
    
    section = props_df.iloc[section_id]
    
    area_mu = section['area'] / np.square(resolution)
    min_diam = section['min'] / resolution
    max_diam = section['max'] / resolution
    eccentricity = section['eccentricity']
    
    section_data = pd.DataFrame(
        {'ID': [im_name], 'area': [area_mu], 'eccentricity': [eccentricity], 'min': [min_diam],
         'max': [max_diam]})
    
    bin_im = section['image']
    bbox = section['bbox']
    
    return section_data, bin_im, bbox


def crop_section(img, im_name, resolution, minpixel, maxpixel, im_center):
    
    try:
        # binarize
        thresh = skimage.filters.threshold_minimum(img)
        bin_img = skimage.segmentation.clear_border(img < thresh)
        # label the image
        label_im, num_elem = skimage.measure.label(bin_img, connectivity=2, return_num=True)
        
        props = skimage.measure.regionprops(label_image=label_im, intensity_image=img)
        
        section_data, bin_im, bbox = section_props(props, im_name, resolution, minpixel, maxpixel, im_center)
        
        pad = 100
        minr = bbox[0] - pad
        minc = bbox[1] - pad
        maxr = bbox[2] + pad
        maxc = bbox[3] + pad
        bbox_pad = [minc, minr, maxc, maxr]
        crop_im = np.asarray(Image.fromarray(img).crop(bbox_pad))
    
    except:
        minr = int(im_center[0] / 2)
        minc = int(im_center[1] / 2)
        maxr = int(im_center[0] * 1.5)
        maxc = int(im_center[1] * 1.5)
        
        bbox_pad = [minc, minr, maxc, maxr]
        # print("Error: \n Found no bbox for {} \n Used center 25% of image instead: {}".format(im_name, str(bbox_pad)))
        
        crop_im = np.asarray(Image.fromarray(img).crop(bbox_pad))
        
    return crop_im

def segment_section(crop_im, im_name, resolution, minpixel, maxpixel, im_center):
    try:
        thresh = skimage.filters.threshold_minimum(crop_im)
        bin_ls_set = crop_im < thresh

        seg_im = skimage.segmentation.morphological_chan_vese(np.asarray(crop_im), 40, init_level_set=bin_ls_set, smoothing=4)
        
        seg_im_inv = np.asarray(seg_im != 0)
        
        crop_label_im, num_elem = skimage.measure.label(seg_im_inv, connectivity=2, return_num=True)
        
        crop_props = skimage.measure.regionprops(label_image=crop_label_im, intensity_image=np.asarray(crop_im))
        
        section_data, bin_im, bbox = section_props(crop_props, im_name, resolution, minpixel, maxpixel, im_center)
    
    except:
        section_data = pd.DataFrame(
            {'ID': [np.nan], 'area': [np.nan], 'eccentricity': [np.nan], 'min': [np.nan],
             'max': [np.nan]})
        thresh = skimage.filters.threshold_minimum(crop_im)
        bin_im = crop_im < thresh
        
    return section_data, bin_im

def save_sections(output_path, im_name, im, save_crop=False):
    if save_crop:
        crop_path = make_subdirectory(output_path, "crop")
        with pathlib.Path(crop_path).joinpath(im_name + ".tiff") as savename:
            try:
                skimage.io.imsave(str(savename), im)
            except AttributeError:
                im.save(savename)
                
    else:
        binary_path = make_subdirectory(output_path, "binary")
        with pathlib.Path(binary_path).joinpath(im_name + ".tiff") as savename:
            im = Image.fromarray(im)
            im.save(savename)
            
# # @timing
@blockPrint
def section_seq(input_file, output_path, resolution, minsize, maxsize, save_img):
    """Segments the input image to isolate the section(s).

    Parameters
    ----------
    img : np.ndarray
        Image to be segmented.

    Returns
    -------
    np.ndarray
        An ndarray of the segmented (binary) image.

    """
    
    with tqdm(total=3, desc="section analysis sequence", unit="steps", position=1, leave=None) as pbar:
        for i in [input_file]:
            
            section_data = pd.DataFrame()
            
            try:

                # read in file
                img, im_name = imread(input_file, use_skimage=True)
            
                # Gets the unique values in the image matrix. Since it is binary, there should only be 2.
                unique, counts = np.unique(img, return_counts=True)
            
                # find center of image
                im_center = list(np.divide(img.shape, 2))  # returns array of two floats
            
                minpixel = minsize * resolution
                maxpixel = maxsize * resolution
                
                pbar.update(1)
                
                if len(unique) == 2:
                    seg_im = skimage.util.invert(img)
                    pbar.update(1)
                    label_im, num_elem = skimage.measure.label(seg_im, connectivity=2, return_num=True)
            
                    props = skimage.measure.regionprops(label_image=label_im, intensity_image=img)
            
                    section_data, bin_im, bbox = section_props(props, im_name, resolution, minpixel, maxpixel, im_center)
    
                    pad = 100
                    minr = bbox[0] - pad
                    minc = bbox[1] - pad
                    maxr = bbox[2] + pad
                    maxc = bbox[3] + pad
                    bbox_pad = [minc, minr, maxc, maxr]
                    crop_im = Image.fromarray(img).crop(bbox_pad)
    
                    if save_img:
                        save_sections(output_path, im_name, crop_im, save_crop=True)
                        save_sections(output_path, im_name, bin_im, save_crop=False)
                    
                    pbar.update(1)
                else:
                    crop_im = crop_section(img, im_name, resolution, minpixel, maxpixel, im_center)
                    pbar.update(1)
            
                    section_data, bin_im = segment_section(crop_im, im_name, resolution, minpixel, maxpixel, im_center)
                    
                    if save_img:
                        save_sections(output_path, im_name, crop_im, save_crop=True)
                        save_sections(output_path, im_name, bin_im, save_crop=False)
                    pbar.update(1)
            except:
                pass
        
            return section_data
    

# # @timing
@blockPrint
def filter_curv(input_file, output_path, save_img):
    """Uses a ridge filter to extract the curved (or straight) lines from the background noise.

    Parameters
    ----------
    input_file : str
        A string path to the input image.
    output_path : str
        A string path to the output directory.
    save_img : bool
        True or False for saving filtered image.

    Returns
    -------
    filter_img: np.ndarray
        The filtered image.
    im_name: str
        A string with the image name.

    """
    
    # create pathlib object for input Image
    input_path = pathlib.Path(input_file)
    
    gray_img, im_name = imread(input_path)
    
    # # extract image name
    # im_name = input_path.stem
    #
    # # read in Image
    # gray_img = cv2.imread(str(input_path), 0)
    # type(gray_img)
    # # print("Image size is:", gray_img.shape)
    
    # use frangi ridge filter to find hairs, the output will be inverted
    filter_img = skimage.filters.frangi(gray_img)
    type(filter_img)
    # print("Image size is:", filter_img.shape)
    
    if save_img:
        output_path = make_subdirectory(output_path, append_name="filtered")
        # inverting and saving the filtered image
        img_inv = skimage.util.invert(filter_img)
        with pathlib.Path(output_path).joinpath(im_name + ".tiff") as save_path:
            plt.imsave(save_path, img_inv, cmap="gray")
    
    return filter_img, im_name


# # @timing
@blockPrint
def binarize_curv(filter_img, im_name, output_path, save_img):
    """Binarizes the filtered output of the fibermorph.filter_curv function.

    Parameters
    ----------
    filter_img : np.ndarray
        Image after ridge filter (float64).
    im_name : str
        Image name.
    output_path : str or pathlib object
        Output directory path.
    save_img : bool
        True or false for saving image.

    Returns
    -------
    np.ndarray
        An array with the binarized image.

    """
    
    selem = skimage.morphology.disk(5)
    
    filter_img = skimage.exposure.adjust_log(filter_img)
    
    try:
        thresh_im = filter_img > filters.threshold_otsu(filter_img)
    except:
        thresh_im = skimage.util.invert(filter_img)
    
    # clear the border of the image (buffer is the px width to be considered as border)
    cleared_im = skimage.segmentation.clear_border(thresh_im, buffer_size=10)
    
    # dilate the hair fibers
    binary_im = scipy.ndimage.binary_dilation(cleared_im, structure=selem, iterations=2)
    
    if save_img:
        output_path = make_subdirectory(output_path, append_name="binarized")
        # invert image
        save_im = skimage.util.invert(binary_im)
        
        # save image
        with pathlib.Path(output_path).joinpath(im_name + ".tiff") as save_name:
            im = Image.fromarray(save_im)
            im.save(save_name)
        return binary_im
    
    else:
        return binary_im


# # @timing
@blockPrint
def remove_particles(img, output_path, name, minpixel, prune, save_img):
    """Removes particles under a particular size in the images.

    Parameters
    ----------
    img : np.ndarray
        Binary image to be cleaned.
    output_path : str or pathlib object
        A path to the output directory.
    name : str
        Input image name.
    minpixel : int
        Minimum pixel size below which elements should be removed.
    prune : bool
        True or false for whether the input is a pruned skeleton.
    save_img : bool
        True or false for saving image.

    Returns
    -------
    np.ndarray
        An array with the noise particles removed.

    """
    img_bool = np.asarray(img, dtype=np.bool)
    img = check_bin(img_bool)

    minimum = minpixel
    # clean = skimage.morphology.diameter_opening(img, diameter_threshold=minimum)
    clean = skimage.morphology.remove_small_objects(img, connectivity=2, min_size=minimum)
        
    if save_img:
        img_inv = skimage.util.invert(clean)
        if prune:
            output_path = make_subdirectory(output_path, append_name="pruned")
        else:
            output_path = make_subdirectory(output_path, append_name="clean")
        with pathlib.Path(output_path).joinpath(name + ".tiff") as savename:
            plt.imsave(savename, img_inv, cmap='gray')
    
    return clean


# # @timing
@blockPrint
def check_bin(img):
    """Checks whether image has been properly binarized. NB: works on the assumption that there should be more
    background pixels than element pixels.

    Parameters
    ----------
    img : np.ndarray
        Description of parameter `img`.

    Returns
    -------
    np.ndarray
        A binary array of the image.

    """
    img_bool = np.asarray(img, dtype=np.bool)
    
    # Gets the unique values in the image matrix. Since it is binary, there should only be 2.
    unique, counts = np.unique(img_bool, return_counts=True)
    # print(unique)
    # print("Found this many counts:")
    # print(len(counts))
    # print(counts)
    
    # If the length of unique is not 2 then print that the image isn't a binary.
    if len(unique) != 2:
        # print("Image is not binarized!")
        hair_pixels = len(counts)
        # print("There is/are {} value(s) present, but there should be 2!\n".format(hair_pixels))
    # If it is binarized, print out that is is and then get the amount of hair pixels to background pixels.
    if counts[0] < counts[1]:
        # print("{} is not reversed".format(str(img)))
        img = skimage.util.invert(img_bool)
        # print("Now {} is reversed =)".format(str(img)))
        return img
    
    else:
        # print("{} is already reversed".format(str(img)))
        img = img_bool
        
        # print(type(img))
        return img


# # @timing
@blockPrint
def skeletonize(clean_img, name, output_path, save_img):
    """Reduces curves and lines to 1 pixel width (skeletons).

    Parameters
    ----------
    clean_img : np.ndarray
        Binary array.
    name : str
        Image name.
    output_path : str or pathlib object.
        Output directory path.
    save_img : bool
        True or false for saving image.

    Returns
    -------
    np.ndarray
        Boolean array of skeletonized image.

    """
    # check if image is binary and properly inverted
    clean_img = check_bin(clean_img)
    
    # skeletonize the hair
    skeleton = skimage.morphology.thin(clean_img)
    
    if save_img:
        output_path = make_subdirectory(output_path, append_name="skeletonized")
        img_inv = skimage.util.invert(skeleton)
        with pathlib.Path(output_path).joinpath(name + ".tiff") as output_path:
            im = Image.fromarray(img_inv)
            im.save(output_path)
        return skeleton
    
    else:
        # print("\n Done skeletonizing {}".format(name))
        
        return skeleton


# # @timing
@blockPrint
def prune(skeleton, name, pruned_dir, save_img):
    """Prunes branches from skeletonized image.
    Adapted from: "http://homepages.inf.ed.ac.uk/rbf/HIPR2/thin.htm"

    Parameters
    ----------
    skeleton : np.ndarray
        Boolean array.
    name : str
        Image name.
    pruned_dir : str or pathlib object
        Output directory path.
    save_img : bool
        True or false for saving image.

    Returns
    -------
    np.ndarray
        Boolean array of pruned skeleton image.

    """
    
    # print("\nPruning {}...\n".format(name))
    
    # identify 3-way branch-points
    hit1 = np.array([[0, 1, 0],
                     [0, 1, 0],
                     [1, 0, 1]], dtype=np.uint8)
    hit2 = np.array([[1, 0, 0],
                     [0, 1, 0],
                     [1, 0, 1]], dtype=np.uint8)
    hit3 = np.array([[1, 0, 0],
                     [0, 1, 1],
                     [0, 1, 0]], dtype=np.uint8)
    hit_list = [hit1, hit2, hit3]
    
    # numpy slicing to create 3 remaining rotations
    for ii in range(9):
        hit_list.append(np.transpose(hit_list[-3])[::-1, ...])
    
    # add structure elements for branch-points four 4-way branchpoints
    hit3 = np.array([[0, 1, 0],
                     [1, 1, 1],
                     [0, 1, 0]], dtype=np.uint8)
    hit4 = np.array([[1, 0, 1],
                     [0, 1, 0],
                     [1, 0, 1]], dtype=np.uint8)
    hit_list.append(hit3)
    hit_list.append(hit4)
    # print("Creating hit and miss list")
    
    skel_image = check_bin(skeleton)
    # print("Converting image to binary array")
    
    branch_points = np.zeros(skel_image.shape)
    # print("Creating empty array for branch points")
    
    for hit in hit_list:
        target = hit.sum()
        curr = ndimage.convolve(skel_image, hit, mode="constant")
        branch_points = np.logical_or(branch_points, np.where(curr == target, 1, 0))
    
    # print("Completed collection of branch points")
    
    # pixels may "hit" multiple structure elements, ensure the output is a binary image
    branch_points_image = np.where(branch_points, 1, 0)
    # print("Ensuring binary")
    
    # use SciPy's ndimage module for locating and determining coordinates of each branch-point
    labels, num_labels = ndimage.label(branch_points_image)
    # print("Labelling branches")
    
    # use SciPy's ndimage module to determine the coordinates/pixel corresponding to the center of mass of each
    # branchpoint
    branch_points = ndimage.center_of_mass(skel_image, labels=labels, index=range(1, num_labels + 1))
    branch_points = np.array([value for value in branch_points if not np.isnan(value[0]) or not np.isnan(value[1])],
                             dtype=int)
    # num_branch_points = len(branch_points)
    
    hit = np.array([[0, 0, 0],
                    [0, 1, 0],
                    [0, 0, 0]], dtype=np.uint8)
    
    dilated_branches = ndimage.convolve(branch_points_image, hit, mode='constant')
    dilated_branches_image = np.where(dilated_branches, 1, 0)
    # print("Ensuring binary dilated branches")
    pruned_image = np.subtract(skel_image, dilated_branches_image)
    # pruned_image = np.subtract(skel_image, branch_points_image)
    
    pruned_image = remove_particles(pruned_image, pruned_dir, name, minpixel=5, prune=True, save_img=save_img)

    return pruned_image


def diag(skeleton):
    """Prunes branches from skeletonized image.
    Adapted from: "http://homepages.inf.ed.ac.uk/rbf/HIPR2/thin.htm"

    Parameters
    ----------
    skeleton : np.ndarray
        Boolean array.
    name : str
        Image name.
    pruned_dir : str or pathlib object
        Output directory path.
    save_img : bool
        True or false for saving image.

    Returns
    -------
    np.ndarray
        Boolean array of pruned skeleton image.

    """
    
    # identify diagonals
    hit1 = np.array([[0, 0, 0],
                     [0, 1, 1],
                     [1, 0, 0]], dtype=np.uint8)
    hit2 = np.array([[1, 0, 0],
                     [0, 1, 1],
                     [0, 0, 0]], dtype=np.uint8)
    hit3 = np.array([[0, 0, 1],
                     [1, 1, 0],
                     [0, 0, 0]], dtype=np.uint8)
    hit4 = np.array([[0, 0, 0],
                     [1, 1, 0],
                     [0, 0, 1]], dtype=np.uint8)
    hit5 = np.array([[0, 1, 0],
                     [0, 1, 0],
                     [1, 0, 0]], dtype=np.uint8)
    hit6 = np.array([[0, 1, 0],
                     [0, 1, 0],
                     [0, 0, 1]], dtype=np.uint8)
    hit7 = np.array([[1, 0, 0],
                     [0, 1, 0],
                     [0, 1, 0]], dtype=np.uint8)
    hit8 = np.array([[0, 0, 1],
                     [0, 1, 0],
                     [0, 1, 0]], dtype=np.uint8)

    mid_list = [hit1, hit2, hit3, hit4, hit5, hit6, hit7, hit8]

    hit9 = np.array([[0, 0, 1],
                     [0, 1, 0],
                     [1, 0, 0]], dtype=np.uint8)
    hit10 = np.array([[1, 0, 0],
                     [0, 1, 0],
                     [0, 0, 1]], dtype=np.uint8)
    
    diag_list = [hit9, hit10]

    hit11 = np.array([[0, 1, 0],
                     [0, 1, 0],
                     [0, 1, 0]], dtype=np.uint8)
    hit12 = np.array([[0, 0, 0],
                      [1, 1, 1],
                      [0, 0, 0]], dtype=np.uint8)
    
    adj_list = [hit11, hit12]
    
    skel_image = check_bin(skeleton).astype(int)
    # print("Converting image to binary array")
    
    diag_points = np.zeros(skel_image.shape)
    mid_points = np.zeros(skel_image.shape)
    adj_points = np.zeros(skel_image.shape)
    # print("Creating empty array for branch points")

    for hit in diag_list:
        target = hit.sum()
        curr = ndimage.convolve(skel_image, hit, mode="constant")
        diag_points = np.logical_or(diag_points, np.where(curr == target, 1, 0))
        
    for hit in mid_list:
        target = hit.sum()
        curr = ndimage.convolve(skel_image, hit, mode="constant")
        mid_points = np.logical_or(mid_points, np.where(curr == target, 1, 0))
        
    for hit in adj_list:
        target = hit.sum()
        curr = ndimage.convolve(skel_image, hit, mode="constant")
        adj_points = np.logical_or(adj_points, np.where(curr == target, 1, 0))

    # pixels may "hit" multiple structure elements, ensure the output is a binary image
    diag_points_image = np.where(diag_points, 1, 0)
    mid_points_image = np.where(mid_points, 1, 0)
    adj_points_image = np.where(adj_points, 1, 0)
    # print("Ensuring binary")

    # use SciPy's ndimage module for locating and determining coordinates of each branch-point
    labels, num_labels = ndimage.label(diag_points_image)
    labels2, num_labels2 = ndimage.label(mid_points_image)
    labels3, num_labels3 = ndimage.label(adj_points_image)
    # print("Labelling branches")

    # use SciPy's ndimage module to determine the coordinates/pixel corresponding to the center of mass of each
    # branchpoint
    diag_points = ndimage.center_of_mass(skel_image, labels=labels, index=range(1, num_labels + 1))
    mid_points = ndimage.center_of_mass(skel_image, labels=labels2, index=range(1, num_labels2 + 1))
    adj_points = ndimage.center_of_mass(skel_image, labels=labels3, index=range(1, num_labels3 + 1))

    diag_points = np.array([value for value in diag_points if not np.isnan(value[0]) or not np.isnan(value[1])], dtype=int)
    mid_points = np.array([value for value in mid_points if not np.isnan(value[0]) or not np.isnan(value[1])], dtype=int)
    adj_points = np.array([value for value in adj_points if not np.isnan(value[0]) or not np.isnan(value[1])],
                          dtype=int)

    num_diag_points = len(diag_points)
    num_mid_points = len(mid_points)
    num_adj_points = len(adj_points)
    
    return num_diag_points, num_mid_points, num_adj_points


# @timing
@blockPrint
def taubin_curv(coords, resolution):
    """Curvature calculation based on algebraic circle fit by Taubin.
    Adapted from: "https://github.com/PmagPy/PmagPy/blob/2efd4a92ddc19c26b953faaa5c08e3d8ebd305c9/SPD/lib
    /lib_curvature.py"
    G. Taubin, "Estimation Of Planar Curves, Surfaces And Nonplanar
                Space Curves Defined By Implicit Equations, With
                Applications To Edge And Range Image Segmentation",
    IEEE Trans. PAMI, Vol. 13, pages 1115-1138, (1991)

    Parameters
    ----------
    coords : list
        Nested list of paired x and y coordinates for each point of the line where a curve needs to be fited.
        [[x_1, y_1], [x_2, y_2], ....]
    resolution : float or int
        Number of pixels per mm in original image.

    Returns
    -------
    float or int(0)
        If the radius of the fitted circle is finite, it will return the curvature (1/radius).
        If the radius is infinite, it will return 0.

    """
    
    warnings.filterwarnings("ignore")  # suppress RuntimeWarnings from dividing by zero
    xy = np.array(coords)
    x = xy[:, 0] - np.mean(xy[:, 0])  # norming points by x avg
    y = xy[:, 1] - np.mean(xy[:, 1])  # norming points by y avg
    # centroid = [np.mean(xy[:, 0]), np.mean(xy[:, 1])]
    z = x * x + y * y
    zmean = np.mean(z)
    z0 = ((z - zmean) / (2. * np.sqrt(zmean)))  # changed from using old_div to Python 3 native division
    zxy = np.array([z0, x, y]).T
    u, s, v = np.linalg.svd(zxy, full_matrices=False)  #
    v = v.transpose()
    a = v[:, 2]
    a[0] = (a[0]) / (2. * np.sqrt(zmean))
    a = np.concatenate([a, [(-1. * zmean * a[0])]], axis=0)
    # a, b = (-1 * a[1:3]) / a[0] / 2 + centroid
    r = np.sqrt(a[1] * a[1] + a[2] * a[2] - 4 * a[0] * a[3]) / abs(a[0]) / 2
    
    if np.isfinite(r):
        curv = 1 / (r / resolution)
        if curv >= 0.00001:
            return curv
        else:
            return 0
    else:
        return 0


# # @timing
@blockPrint
def subset_gen(pixel_length, window_size_px, label):
    """Generator function for start and end indices of the window of measurement.

    Parameters
    ----------
    pixel_length : int
        Number of pixels in input curve/line.
    window_size_px : int
        The size of window of measurement.
    label : np.array
        Nested list of coordinates for the input curve/line.

    Returns
    -------
    list
        Nested list of coordinates for the window of measurement in the input curve/line.

    """
    
    # TODO: Add warning that under 10pixels will yield problems
    subset_start = 0
    if window_size_px >= 10:
        subset_end = int(window_size_px + subset_start)
    else:
        subset_end = int(pixel_length)
    while subset_end <= pixel_length:
        subset = label[subset_start:subset_end]
        yield subset
        subset_start += 1
        subset_end += 1


# # @timing
@blockPrint
def within_element_func(output_path, name, element, taubin_df):
    # for within hair distribution
    label_name = str(element.label)
    element_df = pd.DataFrame(taubin_df)
    element_df.columns = ['curv']
    element_df['label'] = label_name
    
    output_path = make_subdirectory(output_path, append_name="WithinElement")
    with pathlib.Path(output_path).joinpath("WithinElement_" + name + "_Label-" + label_name + ".csv") as save_path:
        element_df.to_csv(save_path)
    
    return True

@blockPrint
def define_structure(structure: str):

    if structure == "mid":
        hit1 = np.array([[0, 0, 0],
                         [0, 1, 1],
                         [1, 0, 0]], dtype=np.uint8)
        hit2 = np.array([[1, 0, 0],
                         [0, 1, 1],
                         [0, 0, 0]], dtype=np.uint8)
        hit3 = np.array([[0, 0, 1],
                         [1, 1, 0],
                         [0, 0, 0]], dtype=np.uint8)
        hit4 = np.array([[0, 0, 0],
                         [1, 1, 0],
                         [0, 0, 1]], dtype=np.uint8)
        hit5 = np.array([[0, 1, 0],
                         [0, 1, 0],
                         [1, 0, 0]], dtype=np.uint8)
        hit6 = np.array([[0, 1, 0],
                         [0, 1, 0],
                         [0, 0, 1]], dtype=np.uint8)
        hit7 = np.array([[1, 0, 0],
                         [0, 1, 0],
                         [0, 1, 0]], dtype=np.uint8)
        hit8 = np.array([[0, 0, 1],
                         [0, 1, 0],
                         [0, 1, 0]], dtype=np.uint8)
        
        mid_list = [hit1, hit2, hit3, hit4, hit5, hit6, hit7, hit8]
        return mid_list
    elif structure == "diag":
        hit1 = np.array([[0, 0, 1],
                         [0, 1, 0],
                         [1, 0, 0]], dtype=np.uint8)
        hit2 = np.array([[1, 0, 0],
                         [0, 1, 0],
                         [0, 0, 1]], dtype=np.uint8)
        diag_list = [hit1, hit2]

        return diag_list

    else:
        raise TypeError(
            "Structure input for find_structure() is invalid, choose from 'mid', or 'diag' and input as str")

@blockPrint
def find_structure(skeleton, structure: str):
    skel_image = check_bin(skeleton).astype(int)
    
    # print(skel_image.shape)
    
    # creating empty array for hit and miss algorithm
    hit_points = np.zeros(skel_image.shape)
    # defining the structure used in hit-and-miss algorithm
    hit_list = define_structure(structure)
    
    for hit in hit_list:
        target = hit.sum()
        curr = ndimage.convolve(skel_image, hit, mode="constant")
        hit_points = np.logical_or(hit_points, np.where(curr == target, 1, 0))
    
    # Ensuring target image is binary
    hit_points_image = np.where(hit_points, 1, 0)
    
    # use SciPy's ndimage module for locating and determining coordinates of each branch-point
    labels, num_labels = ndimage.label(hit_points_image)
    
    return labels, num_labels

@blockPrint
def pixel_length_correction(element):
    
    num_total_points = element.area
    
    skeleton = element.image
    
    diag_points, num_diag_points = find_structure(skeleton, 'diag')
    # print(num_diag_points)
    
    mid_points, num_mid_points = find_structure(skeleton, 'mid')
    # print(num_mid_points)
    
    num_adj_points = num_total_points - num_diag_points - num_mid_points
    # print(num_adj_points)

    corr_element_pixel_length = num_adj_points + (num_diag_points * np.sqrt(2)) + (num_mid_points * np.sqrt(1.25))

    return corr_element_pixel_length

# # @timing
@blockPrint
def analyze_each_curv(element, window_size_px, resolution, output_path, name, within_element):
    """Calculates curvature for each labeled element in an array.

    Parameters
    ----------
    element : Iterable
        A list of RegionProperties (most importantly, coordinates) from scikit-image regionprops function.
    window_size_px : int
        Number of pixels to be used for window of measurement.
    resolution : float
        Number of pixels per mm in original image.

    Returns
    -------
    lst
        A list of the mean and median curvatures and the element length.

    """
    
    element_label = np.array(element.coords)
    
    # Due to the differences in distance for vertically and horizontally vs. diagonally adjacent pixels, a correction
    # is applied of a factor of 1.12. See literature below:
    # Smit AL, Sprangers JFCM, Sablik PW, Groenwold J. Automated measurement of root length with a three-dimensional
    # high-resolution scanner and image analysis. Plant Soil. 1994 Jan 1;158(1):145–9.
    # Smit AL, Bengough AG, Engels C, van Noordwijk M, Pellerin S, van de Geijn SC. Root Methods: A Handbook.
    # Springer Science & Business Media; 2013. 594 p.323
    
    element_pixel_length = int(element.area)  # length of element in pixels

    corr_element_pixel_length = pixel_length_correction(element)

    length_mm = float(corr_element_pixel_length / resolution)
    
    if not window_size_px is None:
        window_size_px = int(window_size_px)
        
        subset_loop = (subset_gen(element_pixel_length, window_size_px, element_label))  # generates subset loop
        
        # Safe generator expression in case of errors
        curv = [taubin_curv(element_coords, resolution) for element_coords in subset_loop]
    
        taubin_df = pd.Series(curv).astype('float')
        # print("\nCurv dataframe is:")
        # print(taubin_df)
        # print(type(taubin_df))
        # print("\nCurv df min is:{}".format(taubin_df.min()))
        # print("\nCurv df max is:{}".format(taubin_df.max()))
        
        # print("\nTrimming outliers...")
        taubin_df2 = taubin_df[taubin_df.between(taubin_df.quantile(.01), taubin_df.quantile(.99))]  # without outliers
        
        # print("\nAfter trimming outliers...")
        # print("\nCurv dataframe is:")
        # print(taubin_df2)
        # print(type(taubin_df2))
        # print("\nCurv df min is:{}".format(taubin_df2.min()))
        # print("\nCurv df max is:{}".format(taubin_df2.max()))
        
        curv_mean = taubin_df2.mean()
        # print("\nCurv mean is:{}".format(curv_mean))
        
        curv_median = taubin_df2.median()
        # print("\nCurv median is:{}".format(curv_median))
        
        within_element_df = [curv_mean, curv_median, length_mm]
        # print("\nThe curvature summary stats for this element are:")
        # print(within_element_df)
        
        if within_element:
            within_element_func(output_path, name, element, taubin_df)
            
        if within_element_df is not None or np.nan:
            return within_element_df
        else:
            pass
        
    elif window_size_px is None:
        curv = taubin_curv(element.coords, resolution)
    
        within_element_df = pd.DataFrame({'curv': [curv], 'length': [length_mm]})
    
        if within_element_df is not None or np.nan:
            return within_element_df
        else:
            pass


# # @timing
@blockPrint
def imread(input_file, use_skimage=False):
    """Reads in image as grayscale array.

    Parameters
    ----------
    input_file : str
        String with path to input file.

    Returns
    -------
    img: array uint8
        A grayscale array based on the input image.
    im_name: str
        A string with the image name.

    """
    input_path = pathlib.Path(input_file)
    if use_skimage:
        try:
            img_float = skimage.io.imread(input_file, as_gray=True)
            img = skimage.img_as_ubyte(img_float)
        except ValueError:
            img = np.array(Image.open(str(input_path)).convert('L'))
    else:
        img = np.array(Image.open(str(input_path)).convert('L'))
    im_name = input_path.stem
    return img, im_name


# # @timing
@blockPrint
def analyze_all_curv(img, name, output_path, resolution, window_size, window_unit, test, within_element):
    """Analyzes curvature for all elements in an image.

    Parameters
    ----------
    img : np.ndarray
        Pruned skeleton of curves/lines as a uint8 ndarray.
    name : str
        Image name.
    output_path : str or pathlib object
        Output directory.
    resolution : int
        Number of pixels per mm in original image.
    window_size: float or int or list
        Desired size for window of measurement in mm.
    test : bool
        True or False for whether this is being run for validation tests
    within_element
        True or False for whether to save spreadsheets with within element curvature values

    Returns
    -------
    pd DataFrame
        Pandas DataFrame with summary data for all elements in image.

    """
    if type(img) != 'np.ndarray':
        print(type(img))
        img = np.array(img)
    else:
        print(type(img))
    
    # print("Analyzing {}".format(name))
    
    img = check_bin(img)
    
    label_image, num_elements = skimage.measure.label(img.astype(int), connectivity=2, return_num=True)
    # print("\n There are {} elements in the image".format(num_elements))
    
    props = skimage.measure.regionprops(label_image)
    
    if not isinstance(window_size, list):
        # print("Window size passed from args is:\n")
        # print(type(window_size))
        # print(window_size)
        # print("First item is:")
        # print(window_size[0])
        
        window_size = [window_size]
        
        # window_size = [float(i) for i in window_size]
        
    name = name
    
    im_sumdf = [window_iter(props, name, i, window_unit, resolution, output_path, test, within_element) for i in window_size]
    
    im_sumdf = pd.concat(im_sumdf)
    
    return im_sumdf

@blockPrint
def window_iter(props, name, window_size, window_unit, resolution, output_path, test, within_element):
    
    tempdf = []
    
    if not window_size is None:
        if not window_unit == "px":
            window_size_px = int(window_size * resolution)
        else:
            window_size_px = int(window_size)
            window_size = int(window_size)
        
        # print("\nWindow size for analysis is {} {}".format(window_size_px, window_unit))
        # print("Analysis of curvature for each element begins...")
        
        name = str(name + "_WindowSize-" + str(window_size) + str(window_unit))
        # print(name)
        # print(window_size)
        
        tempdf = [analyze_each_curv(hair, window_size_px, resolution, output_path, name, within_element) for hair in props if hair.area > window_size]
    
        within_im_curvdf = pd.DataFrame(tempdf, columns=['curv_mean', 'curv_median', 'length'])
        
        within_im_curvdf2 = pd.DataFrame(within_im_curvdf, columns=['curv_mean', 'curv_median', 'length']).dropna()
        
        output_path = make_subdirectory(output_path, append_name="analysis")
        with pathlib.Path(output_path).joinpath("ImageSum_" + name + ".csv") as save_path:
            within_im_curvdf2.to_csv(save_path)
        
        curv_mean_im_mean = within_im_curvdf2['curv_mean'].mean()
        curv_mean_im_median = within_im_curvdf2['curv_mean'].median()
        curv_median_im_mean = within_im_curvdf2['curv_median'].mean()
        curv_median_im_median = within_im_curvdf2['curv_median'].median()
        length_mean = within_im_curvdf2['length'].mean()
        length_median = within_im_curvdf2['length'].median()
        hair_count = len(within_im_curvdf2.index)
        
        im_sumdf = pd.DataFrame(
            {"ID": [name], "curv_mean_mean": [curv_mean_im_mean], "curv_mean_median": [curv_mean_im_median], "curv_median_mean": [curv_median_im_mean], "curv_median_median": [curv_median_im_median], "length_mean": [length_mean],"length_median": [length_median], "hair_count": [hair_count]})

        if test:
            return within_im_curvdf2
        else:
            return im_sumdf
    
    elif window_size is None:
        window_size_px = None
        within_element = None
        minsize = 0.5 * resolution
        tempdf = [analyze_each_curv(hair, window_size_px, resolution, output_path, name, within_element) for hair in
                  props if hair.area > minsize]

        within_im_curvdf = pd.concat(tempdf)

        within_im_curvdf2 = within_im_curvdf.dropna()

        output_path = make_subdirectory(output_path, append_name="analysis")
        with pathlib.Path(output_path).joinpath("ImageSum_" + name + ".csv") as save_path:
            within_im_curvdf2.to_csv(save_path)

        im_mean = within_im_curvdf2['curv'].mean()
        im_median = within_im_curvdf2['curv'].median()
        length_mean = within_im_curvdf2['length'].mean()
        length_median = within_im_curvdf2['length'].median()
        hair_count = len(within_im_curvdf2.index)

        im_sumdf = pd.DataFrame({'ID': name, 'curv_mean': [im_mean], 'curv_median': [im_median], 'length_mean': [length_mean], 'length_median': [length_median], 'hair_count': [hair_count]})
        
        if test:
            return within_im_curvdf2
        else:
            return im_sumdf
    
def curvature_seq(input_file, output_path, resolution, window_size, window_unit, save_img, test, within_element):
    """Sequence of functions to be executed for calculating curvature in fibermorph.

    Parameters
    ----------
    input_file : str or pathlib Path object
        Path to image that needs to be analyzed.
    output_path : str or pathlib Path object
        Output directory
    resolution : int
        Number of pixels per mm in original image.
    window_size : float or float
        Desired size for window of measurement in mm.
    save_img : bool
        True or false for saving images.
    test : bool
        True or false for whether this is being run for validation tests.
    within_element
        True or False for whether to save spreadsheets with within element curvature values

    Returns
    -------
    pd DataFrame
        Pandas DataFrame with curvature summary data for all images.

    """
        
    with tqdm(total=6, desc="curvature analysis sequence", unit="steps", position=1, leave=None) as pbar:
        for i in [input_file]:
        
            # filter
            filter_img, im_name = filter_curv(input_file, output_path, save_img)
            pbar.update(1)
                
            # binarize
            binary_img = binarize_curv(filter_img, im_name, output_path, save_img)
            pbar.update(1)
        
            # remove particles
            clean_im = remove_particles(binary_img, output_path, im_name, minpixel=int(resolution/2), prune=False, save_img=save_img)
            pbar.update(1)
        
            # skeletonize
            skeleton_im = skeletonize(clean_im, im_name, output_path, save_img)
            pbar.update(1)
        
            # prune
            pruned_im = prune(skeleton_im, im_name, output_path, save_img)
            pbar.update(1)
        
            # analyze
            im_df = analyze_all_curv(pruned_im, im_name, output_path, resolution, window_size, window_unit, test, within_element)
            pbar.update(1)
        
    return im_df


# Main modules (organized in order of operations: raw2gray, curvature, section)

def raw2gray(input_directory, output_location, file_type, jobs):
    """Convert raw files to grayscale tiff files.

    Parameters
    ----------
    input_directory : str or pathlib object
        String or pathlib object for input directory containing raw files.
    output_location : str or pathlib object
        String or pathlib object for output directory where converted files should be created.
    file_type : str
        The extension for the raw files (e.g. ".RW2").
    jobs : int
        Number of jobs to run in parallel.

    Returns
    -------
    bool
        True

    """
    total_start = timer()
    
    file_list = [p for p in pathlib.Path(input_directory).rglob('*') if p.suffix in file_type]
    list.sort(file_list)  # sort the files
    # print(file_list)  # printed the sorted files
    
    # print("There are {} files to convert".format(len(file_list)))
    # print("\n\n")
    
    # print("Converting raw files into grayscale tiff files...\n")
    
    tiff_directory = make_subdirectory(output_location, append_name="tiff")
    
    with tqdm_joblib(tqdm(desc="raw2gray", total=len(file_list), unit="files", miniters=1)) as progress_bar:
        progress_bar.monitor_interval = 2
        Parallel(n_jobs=jobs, verbose=0)(delayed(raw_to_gray)(f, tiff_directory) for f in file_list)
    
    # End the timer and then print out the how long it took
    total_end = timer()
    total_time = (total_end - total_start)
    
    # This will print out the minutes to the console, with 2 decimal places.
    tqdm.write("\n\nEntire analysis took: {}\n\n".format(convert(total_time)))
    
    return True


def curvature(input_directory, main_output_path, jobs, resolution, window_size, window_unit, save_img, within_element):
    """Takes directory of grayscale tiff images and analyzes curvature for each curve/line in the image.

    Parameters
    ----------
    input_directory : str or pathlib object
        Input directory path as str or pathlib object.
    main_output_path : str or pathlib object
        Main output path as str or pathlib object.
    jobs : int
        Number of jobs to run in parallel.
    resolution : float
        Number of pixels per mm in original image.
    window_size : float or int
        Desired window of measurement in mm or pixels.
    window_unit : str
        Are the units for the window size in pixels or mm.
    save_img : bool
        True or false for saving images for image processing steps.
    within_element
        True or False for whether to save spreadsheets with within element curvature values

    Returns
    -------
    bool
        True.

    """
    
    total_start = timer()
    
    # create an output directory for the analyses
    jetzt = datetime.now()
    timestamp = jetzt.strftime("%b%d_%H%M_")
    dir_name = str(timestamp + "fibermorph_curvature")
    output_path = make_subdirectory(main_output_path, append_name=dir_name)
    
    file_list = list_images(input_directory)
    
    # List expression for curv df per image
    # im_df = [curvature_seq(input_file, filtered_dir, binary_dir, pruned_dir, clean_dir, skeleton_dir, analysis_dir,
    # resolution, window_size_mm, save_img) for input_file in file_list]
    
    # This is the old parallel jobs function
    with tqdm_joblib(tqdm(desc="curvature", total=len(file_list), unit="files", miniters=1)) as progress_bar:
        progress_bar.monitor_interval = 2
        im_df = (Parallel(n_jobs=jobs, verbose=0)(
            delayed(curvature_seq)(input_file, output_path,
                                   resolution, window_size, window_unit, save_img, test=False, within_element=within_element) for
            input_file in file_list))
    
    summary_df = pd.concat(im_df)
    
    # print("This is the summary dataframe for the current sample")
    # print(summary_df)
    
    # print("You've got data...")
    # print(summary_df)
    
    jetzt = datetime.now()
    timestamp = jetzt.strftime("_%b%d_%H%M")
    
    with pathlib.Path(output_path).joinpath("curvature_summary_data{}.csv".format(timestamp)) as output_path:
        summary_df.to_csv(output_path)
        # print(output_path)
    
    # End the timer and then print out the how long it took
    total_end = timer()
    total_time = (total_end - total_start)
    
    # This will print out the minutes to the console, with 2 decimal places.
    tqdm.write("\n\nComplete analysis took: {}\n\n".format(convert(total_time)))
    
    return True


def section(input_directory, main_output_path, jobs, resolution, minsize, maxsize, save_img):
    """Takes directory of grayscale images (and locates central section where necessary) and analyzes cross-sectional
    properties for each image.

    Parameters
    ----------
    input_directory : str or pathlib object
        Input directory path as str or pathlib object.
    main_output_path : str or pathlib object
        Main output path as str or pathlib object.
    jobs : int
        Number of jobs to run in parallel.
    resolution : float
        Number of pixels per micrometer in the image.
    minsize : int
        Minimum diameter for sections.
    maxsize : int
        Maximum diameter for sections.

    Returns
    -------
    bool
        True.

    """
    
    total_start = timer()
    
    # Change to the folder for reading images
    file_list = list_images(input_directory)
    
    # Shows what is in the file_list. The backslash n prints a new line
    # print("There are {} files in the cropped_list:".format(len(file_list)))
    # print(file_list, "\n\n")
    
    # Creating subdirectories for cropped images
    
    jetzt = datetime.now()
    timestamp = jetzt.strftime("%b%d_%H%M_")
    dir_name = str(timestamp + "fibermorph_section")
    output_path = make_subdirectory(main_output_path, append_name=dir_name)
    
    # section_df = [analyze_section(f, output_im_path, minsize, maxsize, resolution) for f in file_list]
    
    with tqdm_joblib(tqdm(desc="section", total=len(file_list), unit="files", miniters=1)) as progress_bar:
        progress_bar.monitor_interval = 2
        section_df = (Parallel(n_jobs=jobs, verbose=0)(
            delayed(section_seq)(f, output_path, resolution, minsize, maxsize, save_img) for f in file_list))
    
    section_df = pd.concat(section_df).dropna()
    section_df.set_index('ID', inplace=True)
    
    with pathlib.Path(output_path).joinpath("summary_section_data.csv") as df_output_path:
        section_df.to_csv(df_output_path)
    
    # End the timer and then print out the how long it took
    total_end = timer()
    total_time = int(total_end - total_start)
    
    tqdm.write("\n\nComplete analysis took: {}\n\n".format(convert(total_time)))
    
    return True