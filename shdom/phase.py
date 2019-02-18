"""
TODO: sort description

A python wrapper for make_mie_table.f90 by Aviad Levis Technion Inst. of Technology February 2019.
Source Fortran files were created by Frank Evans University of Colorado May 2003.
For source code documentation see: http://nit.colorado.edu/shdom/shdomdoc/makemietable.html

Description taken from make_mie_table.f90:
! Does Mie computations to create a scattering table as a function of
! effective radius for gamma or lognormal size distributions of spherical
! particles.  The particles may be water or ice (in which case the 
! program provides the index of refraction depending on wavelength) or
! "aerosols" (in which case the index of refraction is user specified).
! For water or ice particles the scattering properties may be averaged 
! over the desired spectral range with Planck function weighting.  
! The phase functions in the output scattering table are represented 
! with Legendre series.   The effective radii in the table may be evenly
! or logarithmically spaced.
"""

import core
import numpy as np
from scipy.interpolate import RegularGridInterpolator
from shdom import ScalarField, VectorField, BoundingBox, Grid

class TemperatureProfile(object):
    """TODO"""
    def __init__(self, z_levels, temperatures):
        assert len(z_levels) == len(temperatures), 'Number of z_levels doesnt match the number of temperatures specified.'
        self.units = 'Kelvin'
        self._z_levels = z_levels
        self._temperatures = temperatures
        self._temperature_interpolator = RegularGridInterpolator((z_levels,), temperatures)   
        
    def interpolate_temperatures(self, z_levels):
        return self._temperature_interpolator(z_levels)
    
    
class Mie(object):
    """
    Mie scattering for a particle size distribution. 
    Scattering coefficients are averaged over a range of particle radii and wavelengths.
    """
    def __init__(self):
        self._partype = None
        self._rindex = None
        self._pardens = None
        self._distflag = None
        self._wavelen1 = None
        self._wavelen2 = None
        self._avgflag = None  
        self._deltawave = None
        self._alpha = None
        self._wavelencen = None
        self._reff = None
        self._veff = None
        self._extinct = None
        self._ssalb = None
        self._nleg = None
        self._legcoef = None
    
    def set_parameters(self,
                       wavelength_band,
                       particle_type, 
                       distribution,
                       alpha, 
                       wavelength_averaging=False,
                       wavelength_resolution=0.001):
        """
        Set the Mie parameters to compute a new scattering table.
        
        Parameters
        ----------
        wavelength_band: (float, float)
            (minimum, maximum) wavelength in microns. 
            This defines the spectral band over which to integrate, if both are equal monochrome quantities are computed. 
        particle_type: string
            Options are 'Water' or 'Aerosol'.
        distribution: string
            Particle size-distribution. Options are 'Gamma' or 'Log-normal'. 
            Gamma:
              n(r) = a r^alpha exp(-b*r).
              r - droplet radius.
              a, b, alpha - gamma distribution parameters. 
            Log-normal:
              n(r) = a/r exp( -[ln(r/r0)]^2 / (2*alpha^2) ).
              r0 - logarithmic mode.
              alpha - standard deviation of the log. 
        alpha: float
            Shape parameter for the size distribution. 
            Gamma:
              N = a Gamma(alpha+1)/ b^(alpha+1) - number concentration.
              r_eff = (alpha+3)/b - effective radius.
              v_eff = 1/(alpha+3) - effective variance.
            Log-normal:
              N = sqrt(2*pi)*alpha*a - number concentration. 
              r_eff = r0*exp(2.5*alpha^2) - effective radius.
              v_eff = exp(alpha^2)-1  - effective variance.
        wavelength_averaging: bool
            True - average scattering properties over the wavelength_band.
            False - scattering properties of the central wavelength. 
        wavelength_resolution: float
            The distance between two wavelength samples in the band. Used only if wavelength_averaging is True.
        
        Returns
        -------
        None
            
        Notes
        -----
        Aerosol particle type not supported yet.          
        """
        # Particle type 
        if particle_type == 'Water':
            self._partype = 'W'
            self._rindex = 1.0
            self._pardens = 1.0
        else:
            raise NotImplementedError('Particle type {} not supported'.format(particle_type))
        
        # Size distribution pdf 
        if distribution == 'gamma':
            self._distflag = 'G'
        elif distribution == 'lognormal':
            self._distflag = 'L'
        else:
            raise NotImplementedError('Distribution type {} not supported'.format(distibution))
            
        # Averaging over spectral band or monochrome
        self._wavelen1, self._wavelen2 = wavelength_band
        assert self._wavelen1 <= self._wavelen2, 'Minimum wavelength is smaller than maximum'
        avgflag = 'C'
        if self._wavelen1 == self._wavelen2:
            deltawave = -1
        elif wavelength_averaging:
            avgflag = 'A'
            deltawave = wavelength_resolution
            
        self._avgflag = avgflag  
        self._deltawave = deltawave
        self._alpha = alpha
        
        self._wavelencen = core.get_center_wavelen(
            wavelen1=self._wavelen1, 
            wavelen2=self._wavelen2)
        
    
    def compute_table(self,
                      num_effective_radii,
                      start_effective_radius,
                      end_effective_radius,
                      max_integration_radius): 
        """
        Compute a scattering table where for each effective radius:
          1. Extinction-cross section per 1 unit of mass content [g/m^3](liquid water content for water clouds)  
          2. Single scattering albedo, unitless in the range [0, 1].
          3. Legendre expansion coefficients of the normalized scattering phase function (first coefficient is always 1.0)
          4. Number of Legendre coefficients for each scattering phase function. 
    
        Parameters
        ----------
        num_effective_radii: int
            Number of effective radii for which to compute the table.
        start_effective_radius: int
            The starting (lowest) effective radius in the table.
        end_effective_radius: int
            The ending (highest) effective radius in the table.
        max_integration_radius: int
            The maximum radius for which to integrate over the size-distribution.
            max_integration_radius > end_effective_radius.
        
        Returns
        -------
        None
    
        Notes
        -----
        Running this function may take some time.
        """
        
        assert None not in (self._partype, self._rindex, self._pardens,
                            self._distflag, self._wavelen1, self._wavelen2,
                            self._avgflag, self._deltawave, self._alpha, self._wavelencen), \
               'Mie parameters were not set. Set them using set_parameters() setter.'
        
        self._nretab = num_effective_radii
        self._sretab = start_effective_radius
        self._eretab = end_effective_radius
        self._maxradius = max_integration_radius
        
        # Calculate the maximum size parameter and the max number of Legendre terms
        if self._avgflag == 'A':
            xmax = 2 * np.pi * max_integration_radius / self._wavelen1
        else:
            xmax = 2 * np.pi * max_integration_radius / self._wavelencen
        self._maxleg = int(np.round(2.0 * (xmax + 4.0*xmax**0.3334 + 2.0)))
        
        print('Computing mie table...')
        self._reff, self._extinct, self._ssalb, self._nleg, self._legcoef = core.get_mie_table(
            nretab=self._nretab, 
            maxleg=self._maxleg,
            wavelen1=self._wavelen1, 
            wavelen2=self._wavelen2, 
            wavelencen=self._wavelencen,
            deltawave=self._deltawave, 
            pardens=self._pardens, 
            sretab=self._sretab, 
            eretab=self._eretab, 
            alpha=self._alpha, 
            maxradius=self._maxradius, 
            rindex=self._rindex, 
            partype=self._partype, 
            avgflag=self._avgflag, 
            distflag=self._distflag)
        
        self.init_intepolators()
        print('Done.')
        
    def write_table(self, file_path): 
        """
        Write a pre-computed table to <file_path>. 
    
        Parameters
        ----------
        file_path: str
            Path to file.
      
        Returns
        -------
        None

        Notes
        -----
        This function must be ran after pre-computing a scattering table with compute_table().
        """        
        print('Writing mie table to file: {}'.format(file_path))
        core.write_mie_table(
            mietabfile=file_path,
            wavelen1=self._wavelen1, 
            wavelen2=self._wavelen2,
            deltawave=self._deltawave,
            partype=self._partype,
            pardens=self._pardens, 
            rindex=self._rindex,
            distflag=self._distflag,
            alpha=self._alpha, 
            nretab=self._nretab,
            sretab=self._sretab, 
            eretab=self._eretab,             
            reff=self._reff,
            extinct=self._extinct,
            ssalb=self._ssalb,
            nleg=self._nleg,
            legcoef=self._legcoef,
            maxleg=self._maxleg)
        print('Done.')
     
    def read_table(self, file_path): 
        """
        Read a pre-computed table from <file_path>. 
    
        Parameters
        ----------
        file_path: str
            Path to file.
      
        Returns
        -------
        None

        """   
        
        def read_table_header(file_path):
            wavelen1, wavelen2, deltawave = np.genfromtxt(file_path, max_rows=1, skip_header=1, usecols=(0, 1, 2), dtype=float)
            pardens = np.genfromtxt(file_path, max_rows=1, skip_header=2, usecols=(0), dtype=float)
            partype = np.asscalar(np.genfromtxt(file_path, max_rows=1, skip_header=2, usecols=(1), dtype=str))
            rindex  = np.complex(np.genfromtxt(file_path, max_rows=1, skip_header=3, usecols=(0), dtype=float), 
                                 np.genfromtxt(file_path, max_rows=1, skip_header=3, usecols=(1), dtype=float))
            alpha = np.genfromtxt(file_path, max_rows=1, skip_header=4, usecols=(0), dtype=float)
            
            distribution = np.asscalar(np.genfromtxt(file_path, max_rows=1, skip_header=4, usecols=(1), dtype=str))
            if distribution == 'gamma':
                distflag = 'G'
            elif distribution == 'lognormal':
                distflag = 'L'
            else:
                raise NotImplementedError('Distribution type {} not supported'.format(distibution))
            
            nretab = np.genfromtxt(file_path, max_rows=1, skip_header=5, usecols=(0), dtype=int)
            sretab, eretab = np.genfromtxt(file_path, max_rows=1, skip_header=5, usecols=(1, 2), dtype=float)
            maxleg = np.genfromtxt(file_path, max_rows=1, skip_header=6, usecols=(0), dtype=int)
            
            return wavelen1, wavelen2, deltawave, pardens, partype, rindex, alpha, distflag, nretab, sretab, eretab, maxleg        


        
        print('Reading mie table from file: {}'.format(file_path))
        self._wavelen1, self._wavelen2, self._deltawave, self._pardens, \
            self._partype, self._rindex, self._alpha, self._distflag, \
            self._nretab, self._sretab, self._eretab, self._maxleg = read_table_header(file_path)
        
        self._reff, self._extinct, self._ssalb, self._nleg, self._legcoef = \
            core.read_mie_table(mietabfile=file_path, 
                                nretab=self._nretab, 
                                maxleg=self._maxleg)
        
        self.init_intepolators()
        print('Done.')

    
    def init_intepolators(self):
        assert True not in (self._reff is None, self._extinct is None, self._ssalb is None, self._nleg is None, self._legcoef is None), \
                       'Mie scattering table was not computed or read from file. Using compute_table() or read_table().'   
        self._ext_interpolator = RegularGridInterpolator(points=(self.reff,), 
                                                         values=self.extinct, 
                                                         bounds_error=False, 
                                                         fill_value=0.0)
        self._ssalb_interpolator = RegularGridInterpolator(points=(self.reff,), 
                                                           values=self.ssalb, 
                                                           bounds_error=False, 
                                                           fill_value=0.0) 
        self._legcoef_interpolator = RegularGridInterpolator(points=(self.reff,), 
                                                             values=self.legcoeff.T, 
                                                             bounds_error=False, 
                                                             fill_value=0.0)          
     
    def interpolate_scattering_field(self, lwc, reff):
        """
        TODO: documentation
    
        Parameters
        ----------
        lwc: ScalarField 
            A ScalarField object containting the liquid water content (g/m^3) on a 3D grid
        reff: ScalarField 
            A ScalarField object containting the effective radii (micron) on a 3D grid.

        Returns
        -------
        extinction: ScalarField
            A ScalarField object containting the extinction (1/km) on a 3D grid
        albedo: ScalarField
            A ScalarField object containting the single scattering albedo unitless in range [0, 1] on a 3D grid
        phase: VectorField
            A VectorField object containting the phase function legendre coeffiecients on a 3D grid
        Notes
        -----
        Different grids for lwc and reff is not supported.
        """   
        assert lwc.grid == reff.grid, 'Different grids for lwc and reff is not supported yet'
        grid = lwc.grid
        reff_flat = reff.data.ravel()
        ext_data = lwc.data * self._ext_interpolator(reff_flat).reshape(grid.nx, grid.ny, grid.nz)
        ssalb_data = self._ssalb_interpolator(reff_flat).reshape(grid.nx, grid.ny, grid.nz)
        maxleg = self.nleg.max()
        phase_data = self._legcoef_interpolator(reff_flat).reshape(grid.nx, grid.ny, grid.nz, self.maxleg + 1)[..., :maxleg + 1]
        extinction = ScalarField(grid, ext_data)
        albedo = ScalarField(grid, ssalb_data)
        phase = VectorField(grid, phase_data)
        return extinction, albedo, phase
    
    
    @property
    def reff(self):
        if hasattr(self, '_reff'):
            return self._reff
        else:
            print('Mie table was not computed or loaded')    

    @property
    def veff(self):
        if hasattr(self, '_alpha'):
            if self._distflag == 'G':
                return  1.0/(self._alpha+3.0) 
            elif self._distflag == 'L':
                return np.exp(self._alpha**2) - 1.0             
        else:
            print('Mie table was not computed or loaded')  

    @property
    def extinct(self):
        if hasattr(self, '_extinct'):
            return self._extinct
        else:
            print('Mie table was not computed or loaded') 
            
    @property
    def ssalb(self):
        if hasattr(self, '_ssalb'):
            return self._ssalb
        else:
            print('Mie table was not computed or loaded') 
            
    @property
    def nleg(self):
        if hasattr(self, '_nleg'):
            return self._nleg
        else:
            print('Mie table was not computed or loaded') 
            
    @property
    def legcoeff(self):
        if hasattr(self, '_legcoef'):
            return self._legcoef
        else:
            print('Mie table was not computed or loaded')
            
    @property
    def maxleg(self):
        if hasattr(self, '_maxleg'):
            return self._maxleg
        else:
            print('Mie table was not computed or loaded')            
            
    @property
    def distribution(self):
        if hasattr(self, '_distflag'):
            if self._distflag == 'G':
                return 'Gamma'
            elif self._distflag == 'L':
                return 'Lognormal'
        else:
            print('Mie table was not computed or loaded')            

         
class Rayleigh(object):
    """
    Rayleigh scattering for temperature profile.
    
    Description taken from cloudprp.f:
     Computes the molecular Rayleigh extinction profile EXTRAYL [/km]
     from the temperature profile TEMP [K] at ZLEVELS [km].  Assumes
     a linear lapse rate between levels to compute the pressure at
     each level.  The Rayleigh extinction is proportional to air
     density, with the coefficient RAYLCOEF in [K/(mb km)].

    """
    def __init__(self, wavelength, temperature_profile):
        self._wavelength = wavelength
        self._temperature_profile = temperature_profile
        self._raylcoeff = (2.97e-4) * wavelength**(-4.15 + 0.2 * wavelength)
        self._ssalb = np.array([1.0], dtype=np.float32)
        self._phase = np.array([1.0, 0.0, 0.5], dtype=np.float32)
       
        
    def get_scattering_field(self, z_levels):
        """
        TODO
        """
        temperature_profile = self.temperature_profile.interpolate_temperatures(z_levels)
        nz = len(z_levels)

        extinction_profile = core.rayleigh_extinct(
            nzt=nz,
            zlevels=z_levels,
            temp=temperature_profile,
            raylcoef=self.rayleigh_coefficient
        )
        
        bounding_box = BoundingBox(-np.inf, -np.inf, z_levels[0], np.inf, np.inf, z_levels[-1])
        grid = Grid(bounding_box, 1, 1, nz, z_levels)   
        extinction = ScalarField(grid, extinction_profile.reshape(1,1,-1))
        albedo = ScalarField(grid, np.tile(self.ssalb, (1, 1, nz)))
        phase = VectorField(grid, np.tile(self.phase, (1, 1, nz, 1)))            
      
        return extinction, albedo, phase
        
        
    @property
    def extinction_profile(self):
        if hasattr(self, '_extinction_profile'):
            return self._extinction_profile
        else:
            print('scattering profile was not computed') 
    
    @property
    def ssalb_profile(self):
        if hasattr(self, '_ssalb_profile'):
            return self._ssalb_profile
        else:
            print('scattering profile was not computed')      

    @property
    def phase_profile(self):
        if hasattr(self, '_phase_profile'):
            return self._phase_profile
        else:
            print('scattering profile was not computed')    
        
    @property
    def temperature_profile(self):
        return self._temperature_profile 
    
    @property
    def wavelength(self):
        return self._wavelength    
    
    @property
    def rayleigh_coefficient(self):
        return self._raylcoeff 
    
    @property
    def ssalb(self):
        return self._ssalb      
    
    @property
    def phase(self):
        return self._phase      